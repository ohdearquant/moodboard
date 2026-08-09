import type { Thumbnail } from "./model";

// Transport and decode ceilings are safety boundaries, not aesthetic-quality thresholds. They
// sit far above the engine's governed preview recipe while preventing hostile JSON/base64 input
// from turning one local file into unbounded browser work.
export const MAX_REPORT_BYTES = 128 * 1024 * 1024;
export const MAX_THUMBNAIL_COMPRESSED_BYTES = 16 * 1024 * 1024;
export const MAX_THUMBNAIL_SIDE = 8_192;
export const MAX_THUMBNAIL_PIXELS = 4_096 * 4_096;
export const MAX_THUMBNAIL_DECODED_BYTES = 64 * 1024 * 1024;

export function estimatedBase64Bytes(payload: string): number {
  return Math.ceil((payload.length * 3) / 4);
}

export function thumbnailLimitMessage(thumbnail: Thumbnail): string | null {
  const { width, height, data_base64: payload } = thumbnail;
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 1 || height < 1) {
    return "Thumbnail dimensions are not bounded positive integers.";
  }
  if (width > MAX_THUMBNAIL_SIDE || height > MAX_THUMBNAIL_SIDE) {
    return `Thumbnail dimensions exceed the ${MAX_THUMBNAIL_SIDE}-pixel side limit.`;
  }
  const pixels = width * height;
  if (pixels > MAX_THUMBNAIL_PIXELS) {
    return `Thumbnail dimensions exceed the ${MAX_THUMBNAIL_PIXELS}-pixel decode limit.`;
  }
  if (pixels * 4 > MAX_THUMBNAIL_DECODED_BYTES) {
    return `Thumbnail raster exceeds the ${MAX_THUMBNAIL_DECODED_BYTES}-byte decode limit.`;
  }
  if (estimatedBase64Bytes(payload) > MAX_THUMBNAIL_COMPRESSED_BYTES) {
    return `Thumbnail payload exceeds the ${MAX_THUMBNAIL_COMPRESSED_BYTES}-byte compressed limit.`;
  }
  return null;
}
