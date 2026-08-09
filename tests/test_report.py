"""Properties of the report contract, drawn from ADR-0002 rather than invented.

The properties exercised here are the ones ADR-0002 states as rules the schema exists to
enforce: the two asset states are different shapes and an abstained asset has no score key at
all, the axis vocabulary is an exact set equality checked against the board's own axis list, and
the engine validates its own output before writing so that a failing report is an error rather
than a warning. Several tests mutate a valid document and assert the mutation is caught, with
the unmutated document validating in the same test, because a validator that rejects everything
and a validator that rejects the right thing look identical from a single failing case.
"""

from __future__ import annotations

import base64
import hashlib
import json

import jsonschema
import numpy as np
import pytest

from moodboard.report import (
    AXES,
    AXIS_ORDER,
    SCHEMA_PATH,
    AbstainedAsset,
    Board,
    BoardFit,
    BoardStats,
    Category,
    Comparisons,
    EngineProvenance,
    Exemplar,
    Interval,
    IntervalMethod,
    Leverage,
    ModelProvenance,
    Provenance,
    ReferenceEntry,
    Report,
    Representation,
    ScoredAsset,
    StyleModelInfo,
    Thumbnail,
    Tightness,
    from_json_dict,
    to_json_dict,
    validate_axis_vocabulary,
    validate_report,
    write_report,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reference(index: int) -> ReferenceEntry:
    return ReferenceEntry(
        reference_id=f"r{index}",
        content_sha256=_sha256(f"reference-{index}"),
        mime="image/jpeg",
        width=1600,
        height=1067,
        thumbnail=Thumbnail(
            mime="image/webp",
            width=256,
            height=171,
            data_base64=base64.b64encode(f"thumbnail-{index}".encode()).decode("ascii"),
        ),
    )


def _scored_axes(**overrides: float | None) -> dict[str, float | None]:
    axes: dict[str, float | None] = {"style": 0.24, "palette": 0.1, "tone": 0.2, "composition": 0.3}
    axes.update(overrides)
    return axes


def _abstained_axes() -> dict[str, float | None]:
    return {"style": None, "palette": 0.1, "tone": 0.2, "composition": 0.3}


def _scored_asset(asset_id: str = "a0", **overrides: object) -> ScoredAsset:
    fields: dict[str, object] = {
        "state": "scored",
        "asset_id": asset_id,
        "source": f"assets/{asset_id}.jpg",
        "category_id": "c0",
        "n_local": 24,
        "score": 0.24,
        "interval": Interval(low=0.12, high=0.36, level=0.9, method="loo-jackknife-plus"),
        "rank": 1,
        "axes": _scored_axes(),
        "exemplars": (Exemplar(reference_id="r0", similarity=0.81),),
        "flags": (),
    }
    fields.update(overrides)
    return ScoredAsset(**fields)  # type: ignore[arg-type]


def _abstained_asset(asset_id: str = "a1", **overrides: object) -> AbstainedAsset:
    fields: dict[str, object] = {
        "state": "abstained",
        "asset_id": asset_id,
        "source": f"assets/{asset_id}.jpg",
        "reason": "resolution",
        "explanation": (
            "This board has 10 references, so the finest distinction it can express is about 9%, "
            "and you asked for 5%."
        ),
        "measurement": {
            "n_local": 10,
            "n_eff_local": 8.3,
            "supported_alpha": 0.1075,
            "requested_alpha": 0.05,
        },
        "category_id": "c0",
        "axes": _abstained_axes(),
        "exemplars": (Exemplar(reference_id="r1", similarity=0.44),),
        "flags": ("abstained",),
    }
    fields.update(overrides)
    return AbstainedAsset(**fields)  # type: ignore[arg-type]


def _report(**overrides: object) -> Report:
    references = tuple(_reference(i) for i in range(3))
    fields: dict[str, object] = {
        "schema_version": "1.0",
        "board": Board(
            id=_sha256("board"),
            name="spring-campaign",
            n_references=3,
            n_eff=2.6,
            requested_alpha=0.05,
            supported_alpha=0.0556,
            built_at="2026-08-07T14:00:00Z",
            representation=Representation(
                style=StyleModelInfo(model="classical-v1", revision="1", dim=96),
                axes=AXIS_ORDER,
            ),
            fit=BoardFit(
                metric="cosine",
                k=2,
                cluster_cut=0.35,
                dup_cut=0.05,
                interval=IntervalMethod(method="loo-jackknife-plus", replicates=None, seed=0),
            ),
            categories=(Category(category_id="c0", n_local=3, member_ids=("r0", "r1", "r2")),),
        ),
        "board_stats": BoardStats(
            tightness=Tightness(
                loo_mean=0.31,
                loo_sd=0.07,
                loo_quantiles={"p10": 0.2, "p50": 0.3, "p90": 0.44},
            ),
            leverage=(Leverage(reference_id="r2", delta_tightness=-0.04, rank=1),),
            flags=(),
        ),
        "references": references,
        "assets": (_scored_asset(), _abstained_asset()),
        "comparisons": Comparisons(
            ties=(("a0", "a2"),),
            note="assets whose score-difference interval spans zero are not distinguishable",
        ),
        "provenance": Provenance(
            engine=EngineProvenance(name="moodboard", version="0.1.0"),
            model=ModelProvenance(repo="local/classical", revision="1", sha256="not-a-checkpoint"),
            command="moodboard rank brand.mb assets/",
            seed=0,
            created_at="2026-08-07T14:00:01Z",
        ),
    }
    fields.update(overrides)
    return Report(**fields)  # type: ignore[arg-type]


def _schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate_document(document: dict[str, object]) -> None:
    jsonschema.validate(instance=document, schema=_schema(), cls=jsonschema.Draft202012Validator)


# ---------------------------------------------------------------------------
# The schema file itself
# ---------------------------------------------------------------------------


def test_the_committed_schema_file_is_itself_a_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(_schema())


def test_the_schema_file_is_committed_where_report_py_looks_for_it():
    assert SCHEMA_PATH.is_file()


# ---------------------------------------------------------------------------
# A conforming report validates, in both asset states
# ---------------------------------------------------------------------------


def test_a_report_carrying_both_asset_states_passes_its_own_validation():
    report = _report()
    states = {asset.state for asset in report.assets}
    assert states == {"scored", "abstained"}
    validate_report(report)


def test_the_schema_invariant_holds_on_a_report_of_only_scored_assets():
    validate_report(_report(assets=(_scored_asset("a0"), _scored_asset("a1", rank=2))))


def test_the_schema_invariant_holds_on_a_report_of_only_abstained_assets():
    validate_report(
        _report(
            assets=(
                _abstained_asset("a0"),
                _abstained_asset("a1", reason="far_outlier", flags=("abstained", "far_outlier")),
            )
        )
    )


# ---------------------------------------------------------------------------
# The discriminated union: two states are two shapes
# ---------------------------------------------------------------------------


def test_an_abstained_asset_serialises_with_no_score_key_at_all():
    document = to_json_dict(_report())
    abstained = document["assets"][1]
    assert abstained["state"] == "abstained"
    for absent in ("score", "interval", "rank"):
        assert absent not in abstained


def test_a_scored_asset_serialises_with_no_abstention_keys():
    document = to_json_dict(_report())
    scored = document["assets"][0]
    assert scored["state"] == "scored"
    for absent in ("reason", "explanation", "measurement"):
        assert absent not in scored


def test_the_schema_rejects_an_asset_that_carries_both_branches():
    document = to_json_dict(_report())
    _validate_document(document)
    document["assets"][0]["reason"] = "resolution"
    with pytest.raises(jsonschema.ValidationError):
        _validate_document(document)


def test_the_schema_rejects_a_null_score_standing_in_for_an_abstention():
    document = to_json_dict(_report())
    _validate_document(document)
    document["assets"][0]["score"] = None
    with pytest.raises(jsonschema.ValidationError):
        _validate_document(document)


def test_the_schema_rejects_a_score_of_zero_which_a_conformal_p_value_cannot_be():
    document = to_json_dict(_report())
    _validate_document(document)
    document["assets"][0]["score"] = 0.0
    with pytest.raises(jsonschema.ValidationError):
        _validate_document(document)


def test_the_schema_requires_a_null_style_axis_on_an_abstained_asset():
    document = to_json_dict(_report())
    _validate_document(document)
    document["assets"][1]["axes"]["style"] = 0.24
    with pytest.raises(jsonschema.ValidationError):
        _validate_document(document)


def test_the_dataclasses_refuse_a_mismatched_discriminator():
    with pytest.raises(ValueError, match="discriminator"):
        _scored_asset(state="abstained")
    with pytest.raises(ValueError, match="discriminator"):
        _abstained_asset(state="scored")


# ---------------------------------------------------------------------------
# The axis-vocabulary invariant
# ---------------------------------------------------------------------------


def test_today_the_invariant_is_the_exact_four_key_set():
    report = _report()
    assert set(report.board.representation.axes) == {"palette", "tone", "composition"}
    assert set(AXES) == {"palette", "tone", "composition"}
    expected = {"style", "palette", "tone", "composition"}
    for asset in report.assets:
        assert set(asset.axes.keys()) == expected
    validate_axis_vocabulary(report)


def test_a_missing_axis_key_is_a_violation():
    axes = _scored_axes()
    del axes["tone"]
    with pytest.raises(ValueError, match="a0"):
        validate_axis_vocabulary(_report(assets=(_scored_asset("a0", axes=axes),)))


def test_an_extra_axis_key_is_a_violation():
    with pytest.raises(ValueError, match="texture"):
        validate_axis_vocabulary(
            _report(assets=(_scored_asset("a0", axes=_scored_axes(texture=0.5)),))
        )


def test_every_offending_asset_id_is_listed_not_only_the_first():
    short = _scored_axes()
    del short["palette"]
    report = _report(
        assets=(
            _scored_asset("a0", axes=short),
            _scored_asset("a1", axes=_scored_axes(texture=0.5), rank=2),
            _scored_asset("a2", rank=3),
        )
    )
    with pytest.raises(ValueError) as caught:
        validate_axis_vocabulary(report)
    message = str(caught.value)
    assert "a0" in message
    assert "a1" in message
    assert "a2" not in message


def test_a_null_style_satisfies_the_invariant_and_a_missing_style_key_does_not():
    validate_axis_vocabulary(_report(assets=(_abstained_asset("a1"),)))
    axes = _abstained_axes()
    del axes["style"]
    with pytest.raises(ValueError, match="style"):
        validate_axis_vocabulary(_report(assets=(_abstained_asset("a1", axes=axes),)))


def test_the_invariant_follows_the_boards_axis_list_and_not_the_module_constant():
    """A report that dropped an axis is checked against what it carries.

    ADR-0003 allows an axis that fails its intervention test to come out of the report, so a
    two-axis board with two-axis assets is conforming, and the same assets under a three-axis
    board are not. Checking the AXES constant instead would invert both of these.
    """
    two_axis_board = _report().board
    board = Board(
        id=two_axis_board.id,
        name=two_axis_board.name,
        n_references=two_axis_board.n_references,
        n_eff=two_axis_board.n_eff,
        requested_alpha=two_axis_board.requested_alpha,
        supported_alpha=two_axis_board.supported_alpha,
        built_at=two_axis_board.built_at,
        representation=Representation(
            style=two_axis_board.representation.style, axes=("palette", "tone")
        ),
        fit=two_axis_board.fit,
        categories=two_axis_board.categories,
    )
    dropped = {"style": 0.24, "palette": 0.1, "tone": 0.2}
    validate_report(_report(board=board, assets=(_scored_asset("a0", axes=dropped),)))
    with pytest.raises(ValueError, match="composition"):
        validate_axis_vocabulary(_report(board=board, assets=(_scored_asset("a0"),)))


# ---------------------------------------------------------------------------
# Serialisation and round trip
# ---------------------------------------------------------------------------


def test_the_document_round_trips_back_to_an_equal_report():
    report = _report()
    assert from_json_dict(to_json_dict(report)) == report


def test_the_serialised_document_is_json_and_survives_a_json_round_trip():
    document = to_json_dict(_report())
    assert json.loads(json.dumps(document)) == document


def test_numpy_scalars_become_json_numbers():
    """The computing modules produce numpy scalars, and json.dump cannot serialise a float32."""
    report = _report(
        assets=(
            _scored_asset(
                "a0",
                score=np.float32(0.24),
                n_local=np.int64(24),
                axes={
                    "style": np.float32(0.24),
                    "palette": np.float64(0.1),
                    "tone": 0.2,
                    "composition": 0.3,
                },
            ),
        )
    )
    document = to_json_dict(report)
    assert type(document["assets"][0]["score"]) is float
    assert type(document["assets"][0]["n_local"]) is int
    json.dumps(document)
    validate_report(report)


def test_a_non_integer_where_an_integer_belongs_is_refused_rather_than_truncated():
    with pytest.raises(TypeError, match="rank"):
        to_json_dict(_report(assets=(_scored_asset("a0", rank=1.7),)))


def test_an_unserialisable_measurement_value_is_refused_at_the_boundary():
    with pytest.raises(TypeError, match="measurement"):
        to_json_dict(_report(assets=(_abstained_asset("a1", measurement={"n_local": object()}),)))


def test_a_numpy_scalar_inside_a_measurement_is_coerced():
    report = _report(
        assets=(
            _abstained_asset(
                "a1",
                measurement={"n_local": np.int64(10), "supported_alpha": np.float32(0.1075)},
            ),
        )
    )
    document = to_json_dict(report)
    measurement = document["assets"][0]["measurement"]
    assert type(measurement["n_local"]) is int
    assert type(measurement["supported_alpha"]) is float
    validate_report(report)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_write_report_writes_a_document_that_reloads_to_an_equal_report(tmp_path):
    path = tmp_path / "report.json"
    report = _report()
    write_report(report, path)
    assert from_json_dict(json.loads(path.read_text(encoding="utf-8"))) == report


def test_write_report_leaves_no_file_when_the_axis_invariant_fails(tmp_path):
    path = tmp_path / "report.json"
    axes = _scored_axes()
    del axes["tone"]
    with pytest.raises(ValueError):
        write_report(_report(assets=(_scored_asset("a0", axes=axes),)), path)
    assert not path.exists()


def test_write_report_leaves_no_file_when_the_schema_fails(tmp_path):
    path = tmp_path / "report.json"
    with pytest.raises(jsonschema.ValidationError):
        write_report(_report(assets=(_scored_asset("a0", score=1.5),)), path)
    assert not path.exists()


# ---------------------------------------------------------------------------
# Version and fit fields
# ---------------------------------------------------------------------------


def test_the_schema_pins_the_version_string():
    document = to_json_dict(_report())
    _validate_document(document)
    document["schema_version"] = "1.1"
    with pytest.raises(jsonschema.ValidationError):
        _validate_document(document)


def test_the_schema_requires_replicates_to_be_null():
    document = to_json_dict(_report())
    _validate_document(document)
    document["board"]["fit"]["interval"]["replicates"] = 1000
    with pytest.raises(jsonschema.ValidationError):
        _validate_document(document)


def test_the_schema_rejects_an_unknown_top_level_field():
    document = to_json_dict(_report())
    _validate_document(document)
    document["combined_score"] = 0.5
    with pytest.raises(jsonschema.ValidationError):
        _validate_document(document)


def test_the_schema_rejects_a_timestamp_that_is_not_rfc_3339():
    document = to_json_dict(_report())
    _validate_document(document)
    document["provenance"]["created_at"] = "7 August 2026"
    with pytest.raises(jsonschema.ValidationError):
        _validate_document(document)


def test_the_schema_rejects_a_tie_that_is_not_a_pair():
    document = to_json_dict(_report())
    _validate_document(document)
    document["comparisons"]["ties"] = [["a0", "a1", "a2"]]
    with pytest.raises(jsonschema.ValidationError):
        _validate_document(document)


def test_the_cross_validator_rejects_duplicate_exemplar_ids_in_frozen_v1_0():
    duplicate = (
        Exemplar(reference_id="r0", similarity=0.9),
        Exemplar(reference_id="r0", similarity=0.8),
    )
    with pytest.raises(ValueError, match="duplicate exemplar"):
        validate_report(_report(assets=(_scored_asset(exemplars=duplicate),)))
