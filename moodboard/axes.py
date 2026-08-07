"""The three classical style axes: palette, tone and composition.

ADR-0003 requires each axis to answer a narrow, nameable question about how an image looks,
never what it depicts, and to be checkable by a designer without any explanation. Each
`*_distance` function compares two images directly and returns a scalar in [0, 1]. Each
`*_feature_vector` function embeds one image into a fixed-length descriptor for
`encoders.ClassicalEncoder`. The two are related by measuring the same perceptual property and
are not required to agree numerically, per INTERFACES.md: a variable-shape earth mover's
comparison and a fixed-length histogram embedding are different computations over the same
source data.

Every image is accepted as an HWC array, uint8 in [0, 255] or float in [0, 1]. All internal
work happens on a canonical, resized copy of the image, which bounds the cost of each function
and makes the axes comparable across images shot at different native resolutions. Clustering
uses a fixed seed; everything else here is deterministic by construction (histograms, linear
filters, an FFT-based saliency estimate).
"""

import warnings

import numpy as np
from scipy.cluster.vq import kmeans2
from scipy.ndimage import gaussian_filter, uniform_filter
from scipy.optimize import linprog
from scipy.stats import wasserstein_distance
from skimage.color import rgb2gray, rgb2lab
from skimage.transform import resize

__all__ = [
    "palette_distance",
    "tone_distance",
    "composition_distance",
    "palette_feature_vector",
    "tone_feature_vector",
    "composition_feature_vector",
]

# Palette: dominant-colour clustering (for the distance) and a quantised colour histogram
# (for the embedding). Lab ranges follow the sRGB gamut under CIE D65, the illuminant
# scikit-image's rgb2lab assumes.
_PALETTE_CLUSTERS = 5
_PALETTE_CLUSTER_SEED = 0
_PALETTE_DOWNSAMPLE_SIDE = 128
_PALETTE_HIST_BINS = (4, 4, 4)
_PALETTE_HIST_RANGE = ((0.0, 100.0), (-128.0, 127.0), (-128.0, 127.0))

# Tone: luminance and local-contrast distributions, both read off a canonical-size grid so a
# fixed pixel window means the same thing on every image.
_TONE_CANONICAL_SIDE = 128
_TONE_CONTRAST_WINDOW = 5
_TONE_MAX_CONTRAST = 0.5  # the largest possible local std of a signal confined to [0, 1]
_TONE_HIST_BINS = 16

# Composition: a spectral-residual saliency map (Hou & Zhang, 2007), read coarsely as a
# placement grid and a negative-space ratio, per ADR-0003's "coarsely" instruction.
_SALIENCY_CANONICAL_SIDE = 64
_SALIENCY_SMOOTH_SIGMA = 3.0
_SALIENCY_GRID_SIDE = 4
_NEGATIVE_SPACE_THRESHOLD = 0.15


def _to_float01(image: np.ndarray) -> np.ndarray:
    """Cast to float64 in [0, 1] regardless of whether the input was uint8 or already float."""
    arr = np.asarray(image)
    if np.issubdtype(arr.dtype, np.integer):
        arr = arr.astype(np.float64) / 255.0
    else:
        arr = arr.astype(np.float64)
    return np.clip(arr, 0.0, 1.0)


def _to_rgb(image: np.ndarray) -> np.ndarray:
    """Normalise to an (H, W, 3) float array in [0, 1], dropping alpha or replicating gray."""
    arr = _to_float01(image)
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    elif arr.ndim == 3 and arr.shape[-1] == 1:
        arr = np.repeat(arr, 3, axis=-1)
    elif arr.ndim == 3 and arr.shape[-1] == 4:
        arr = arr[..., :3]
    return arr


def _resize_to_square(image: np.ndarray, side: int) -> np.ndarray:
    return resize(image, (side, side), anti_aliasing=True, mode="reflect")


def _downsample_rgb(image: np.ndarray, max_side: int) -> np.ndarray:
    """Shrink to at most `max_side` on the long edge, preserving aspect ratio. A no-op if the
    image is already smaller, since clustering on very few pixels needs no thumbnailing."""
    rgb = _to_rgb(image)
    height, width = rgb.shape[:2]
    scale = max_side / max(height, width)
    if scale >= 1.0:
        return rgb
    new_height = max(1, round(height * scale))
    new_width = max(1, round(width * scale))
    return resize(rgb, (new_height, new_width), anti_aliasing=True, mode="reflect")


def _luminance01(image: np.ndarray) -> np.ndarray:
    """The CIELAB L channel, rescaled from its native [0, 100] to [0, 1]."""
    lab = rgb2lab(_to_rgb(image))
    return lab[..., 0] / 100.0


# --- Palette -----------------------------------------------------------------------------


def _dominant_colours(
    image: np.ndarray, k: int = _PALETTE_CLUSTERS, seed: int = _PALETTE_CLUSTER_SEED
) -> tuple[np.ndarray, np.ndarray]:
    """Cluster the image's pixels in Lab space into at most `k` weighted dominant colours.
    Returns (centroids, weights) with weights summing to 1. Deterministic for a fixed seed."""
    rgb = _downsample_rgb(image, _PALETTE_DOWNSAMPLE_SIDE)
    lab = rgb2lab(rgb).reshape(-1, 3)

    n_clusters = min(k, lab.shape[0])
    if n_clusters <= 1:
        return lab.mean(axis=0, keepdims=True), np.array([1.0])

    try:
        # A near-uniform image (few or one distinct colour) leaves kmeans++ unable to place
        # every centroid on a distinct point; it emits a RuntimeWarning and an empty-cluster
        # UserWarning rather than failing. The `occupied` filter below is exactly the handling
        # for that case, so the warning is expected noise here, not a signal to surface.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            warnings.simplefilter("ignore", category=UserWarning)
            centroids, labels = kmeans2(lab, n_clusters, minit="++", seed=seed, missing="warn")
    except Exception:
        # Degenerate input (e.g. every pixel identical) can leave kmeans2 unable to seed
        # distinct starting centroids. The image has effectively one dominant colour.
        return lab.mean(axis=0, keepdims=True), np.array([1.0])

    counts = np.bincount(labels, minlength=n_clusters).astype(np.float64)
    occupied = counts > 0
    if not occupied.any():
        return lab.mean(axis=0, keepdims=True), np.array([1.0])
    centroids = centroids[occupied]
    counts = counts[occupied]
    return centroids, counts / counts.sum()


def _earth_movers_distance(
    points_a: np.ndarray, weights_a: np.ndarray, points_b: np.ndarray, weights_b: np.ndarray
) -> float:
    """Exact optimal-transport cost between two weighted point sets (weights each summing to
    1), normalised to [0, 1] by the largest pairwise distance the comparison admits: since the
    two masses are equal, no feasible transport plan can cost more than moving all of it at the
    single most expensive distance available."""
    cost = np.linalg.norm(points_a[:, None, :] - points_b[None, :, :], axis=-1)
    max_cost = cost.max()
    if max_cost <= 0.0:
        return 0.0

    n, m = cost.shape
    a_eq = np.zeros((n + m, n * m))
    for i in range(n):
        a_eq[i, i * m : (i + 1) * m] = 1.0
    for j in range(m):
        a_eq[n + j, j::m] = 1.0
    b_eq = np.concatenate([weights_a, weights_b])

    result = linprog(cost.ravel(), A_eq=a_eq, b_eq=b_eq, bounds=(0, None), method="highs")
    if not result.success:
        raise RuntimeError(f"optimal transport failed to solve: {result.message}")
    return float(result.fun / max_cost)


def palette_distance(image_a: np.ndarray, image_b: np.ndarray) -> float:
    """Dominant colours in CIELAB compared by earth mover's distance, returned in [0, 1] by
    normalising against the maximum transport cost the comparison admits, so values are
    comparable across image pairs. Deterministic: any internal clustering uses a fixed seed."""
    colours_a, weights_a = _dominant_colours(image_a)
    colours_b, weights_b = _dominant_colours(image_b)
    distance = _earth_movers_distance(colours_a, weights_a, colours_b, weights_b)
    return float(np.clip(distance, 0.0, 1.0))


def palette_feature_vector(image: np.ndarray) -> np.ndarray:
    """A fixed-length descriptor for embedding purposes: a quantised CIELAB colour-histogram
    with a fixed bin count, chosen so every image maps to the same-length vector regardless of
    how many distinct dominant colours it has. This is deliberately not the variable-length
    cluster-and-mass representation palette_distance may use internally; a fixed length is
    what makes concatenation and a single L2 normalisation in ClassicalEncoder well-defined.
    Unnormalised; ClassicalEncoder normalises the full concatenation once, not each part."""
    rgb = _downsample_rgb(image, _PALETTE_DOWNSAMPLE_SIDE)
    lab = rgb2lab(rgb).reshape(-1, 3)
    histogram, _ = np.histogramdd(lab, bins=_PALETTE_HIST_BINS, range=_PALETTE_HIST_RANGE)
    return histogram.astype(np.float64).ravel()


# --- Tone ----------------------------------------------------------------------------------


def _canonical_luminance01(image: np.ndarray, side: int = _TONE_CANONICAL_SIDE) -> np.ndarray:
    return _resize_to_square(_luminance01(image), side)


def _local_contrast01(luminance: np.ndarray, window: int = _TONE_CONTRAST_WINDOW) -> np.ndarray:
    """Local standard deviation of a [0, 1]-ranged luminance field over a `window`-pixel box,
    computed from local first and second moments so the whole field is a vectorised pass."""
    w = max(1, min(window, min(luminance.shape)))
    mean = uniform_filter(luminance, size=w, mode="reflect")
    mean_of_squares = uniform_filter(luminance**2, size=w, mode="reflect")
    variance = np.clip(mean_of_squares - mean**2, 0.0, None)
    return np.sqrt(variance)


def tone_distance(image_a: np.ndarray, image_b: np.ndarray) -> float:
    """A distance between luminance and local-contrast distributions, in [0, 1]. Both terms are
    a 1-D earth mover's distance (scipy's exact form) over values already confined to a known
    range, so each term is bounded on its own before the two are averaged."""
    luminance_a = _canonical_luminance01(image_a)
    luminance_b = _canonical_luminance01(image_b)
    luminance_term = wasserstein_distance(luminance_a.ravel(), luminance_b.ravel())

    contrast_a = _local_contrast01(luminance_a).ravel()
    contrast_b = _local_contrast01(luminance_b).ravel()
    contrast_term = wasserstein_distance(contrast_a, contrast_b) / _TONE_MAX_CONTRAST

    combined = 0.5 * luminance_term + 0.5 * min(contrast_term, 1.0)
    return float(np.clip(combined, 0.0, 1.0))


def tone_feature_vector(image: np.ndarray) -> np.ndarray:
    """A fixed-length luminance/local-contrast histogram descriptor."""
    luminance = _canonical_luminance01(image)
    contrast = _local_contrast01(luminance)

    luminance_hist, _ = np.histogram(luminance.ravel(), bins=_TONE_HIST_BINS, range=(0.0, 1.0))
    contrast_hist, _ = np.histogram(
        contrast.ravel(), bins=_TONE_HIST_BINS, range=(0.0, _TONE_MAX_CONTRAST)
    )
    return np.concatenate([luminance_hist.astype(np.float64), contrast_hist.astype(np.float64)])


# --- Composition -----------------------------------------------------------------------------


def _saliency_map01(image: np.ndarray, side: int = _SALIENCY_CANONICAL_SIDE) -> np.ndarray:
    """Spectral-residual saliency (Hou & Zhang, 2007): the log-amplitude spectrum minus its own
    local average marks the frequencies that stand out from the image's typical spectral shape;
    reinstating the original phase and inverse-transforming turns that residual back into a
    spatial map of where those frequencies live. Purely classical (an FFT and two filters), no
    learned weights. Normalised to [0, 1]; a spatially uniform input has no residual and maps
    to an all-zero saliency map."""
    gray = _resize_to_square(rgb2gray(_to_rgb(image)), side)
    spectrum = np.fft.fft2(gray)
    amplitude = np.abs(spectrum)
    phase = np.angle(spectrum)
    log_amplitude = np.log1p(amplitude)
    spectral_residual = log_amplitude - uniform_filter(log_amplitude, size=3, mode="reflect")
    reconstructed = np.fft.ifft2(np.exp(spectral_residual + 1j * phase))
    saliency = gaussian_filter(np.abs(reconstructed) ** 2, sigma=_SALIENCY_SMOOTH_SIGMA)

    low, high = saliency.min(), saliency.max()
    if high - low <= 0.0:
        return np.zeros_like(saliency)
    return (saliency - low) / (high - low)


def _saliency_placement_grid(saliency: np.ndarray, side: int = _SALIENCY_GRID_SIDE) -> np.ndarray:
    """Pool the saliency map into a coarse side-by-side grid of mass, normalised to sum to 1
    so two grids can be compared as distributions over the same fixed set of regions."""
    grid = np.clip(_resize_to_square(saliency, side), 0.0, None)
    total = grid.sum()
    if total > 0.0:
        grid = grid / total
    return grid.ravel()


def _negative_space_ratio(
    saliency: np.ndarray, threshold: float = _NEGATIVE_SPACE_THRESHOLD
) -> float:
    """The fraction of the frame carrying little enough saliency to read as empty background."""
    return float(np.mean(saliency < threshold))


def composition_distance(image_a: np.ndarray, image_b: np.ndarray) -> float:
    """Saliency placement and negative-space ratio, compared coarsely, in [0, 1]. Placement is
    the total-variation distance between two saliency-mass grids (each a proper distribution
    over the same fixed regions, so the distance is bounded by construction); negative space is
    the absolute difference of two ratios, itself bounded. The two are averaged."""
    saliency_a = _saliency_map01(image_a)
    saliency_b = _saliency_map01(image_b)

    grid_a = _saliency_placement_grid(saliency_a)
    grid_b = _saliency_placement_grid(saliency_b)
    placement_term = 0.5 * np.abs(grid_a - grid_b).sum()

    negative_space_term = abs(_negative_space_ratio(saliency_a) - _negative_space_ratio(saliency_b))

    combined = 0.5 * placement_term + 0.5 * negative_space_term
    return float(np.clip(combined, 0.0, 1.0))


def composition_feature_vector(image: np.ndarray) -> np.ndarray:
    """A fixed-length descriptor of saliency placement and negative-space ratio: a coarse
    fixed-size spatial grid of saliency mass, with the negative-space ratio appended as one
    extra component."""
    saliency = _saliency_map01(image)
    grid = _saliency_placement_grid(saliency)
    negative_space = np.array([_negative_space_ratio(saliency)])
    return np.concatenate([grid, negative_space]).astype(np.float64)
