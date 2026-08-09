"""Report 1.1 is additive, strict, and keeps report 1.0 frozen.

The positive fixture is assembled through the typed report boundary.  Negative cases mutate its
serialized document one field at a time, matching ADR-0008's contract-verification method.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import shlex
from dataclasses import replace

import jsonschema
import pytest
from PIL import Image

from moodboard.report import (
    AXIS_ORDER,
    SCHEMA_PATH,
    SCHEMA_PATH_V1_1,
    AbstainedAssetV1_1,
    AxisDefinition,
    BoardFitV1_1,
    BoardStats,
    BoardV1_1,
    CandidateImage,
    CandidateImageInput,
    Category,
    Comparisons,
    EngineProvenanceV1_1,
    EngineSourceProvenance,
    Exemplar,
    Interval,
    IntervalMethod,
    Leverage,
    ModelProvenance,
    ProvenanceV1_1,
    ReferenceEntry,
    ReportV1_1,
    RepresentationV1_1,
    SchemaProvenance,
    ScoredAssetV1_1,
    StyleModelInfo,
    Thumbnail,
    Tightness,
    UnsupportedSchemaVersionError,
    axis_definitions_for,
    from_json_dict,
    report_schema_sha256,
    to_json_dict,
    validate_report,
    write_report,
)


def _sha256(value: str | bytes) -> str:
    payload = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _png(width: int = 3, height: int = 2, colour: tuple[int, int, int] = (20, 40, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def _thumbnail(index: int) -> Thumbnail:
    payload = _png(colour=(20 + index, 40 + index, 60 + index))
    return Thumbnail(
        mime="image/png",
        width=3,
        height=2,
        data_base64=base64.b64encode(payload).decode("ascii"),
    )


def _reference(index: int) -> ReferenceEntry:
    return ReferenceEntry(
        reference_id=f"r{index}",
        content_sha256=_sha256(f"reference-{index}"),
        mime="image/png",
        width=30,
        height=20,
        thumbnail=_thumbnail(index),
    )


def _candidate_image(index: int) -> CandidateImage:
    return CandidateImage(
        content_sha256=_sha256(f"candidate-{index}"),
        mime="image/png",
        width=30,
        height=20,
        thumbnail=_thumbnail(10 + index),
    )


def _exemplars() -> tuple[Exemplar, ...]:
    return (
        Exemplar(reference_id="r2", similarity=0.9),
        Exemplar(reference_id="r0", similarity=0.8),
        Exemplar(reference_id="r1", similarity=0.7),
    )


def _scored_asset(**overrides: object) -> ScoredAssetV1_1:
    fields: dict[str, object] = {
        "state": "scored",
        "asset_id": "a0",
        "source": "assets/a0.png",
        "image": _candidate_image(0),
        "category_id": "c0",
        "n_local": 3,
        "score": 0.75,
        "interval": Interval(low=0.5, high=1.0, level=0.9, method="loo-jackknife-plus"),
        "rank": 1,
        "axes": {"style": 0.75, "palette": 0.1, "tone": 0.2, "composition": 0.3},
        "exemplars": _exemplars(),
        "flags": (),
    }
    fields.update(overrides)
    return ScoredAssetV1_1(**fields)  # type: ignore[arg-type]


def _abstained_asset(**overrides: object) -> AbstainedAssetV1_1:
    fields: dict[str, object] = {
        "state": "abstained",
        "asset_id": "a1",
        "source": "assets/a1.png",
        "image": _candidate_image(1),
        "reason": "resolution",
        "explanation": "This board cannot honour the requested resolution.",
        "measurement": {"n_local": 3, "requested_alpha": 0.05},
        "category_id": "c0",
        "axes": {"style": None, "palette": 0.2, "tone": 0.3, "composition": 0.4},
        "exemplars": _exemplars(),
        "flags": ("abstained",),
    }
    fields.update(overrides)
    return AbstainedAssetV1_1(**fields)  # type: ignore[arg-type]


def _report(**overrides: object) -> ReportV1_1:
    argv = ("moodboard", "rank", "assets", "-b", "brand.mb")
    fields: dict[str, object] = {
        "schema_version": "1.1",
        "board": BoardV1_1(
            id=_sha256("board"),
            name="spring-campaign",
            n_references=3,
            n_eff=2.6,
            requested_alpha=0.05,
            supported_alpha=0.0556,
            built_at="2026-08-08T14:00:00Z",
            representation=RepresentationV1_1(
                style=StyleModelInfo(model="classical-v1", revision="2", dim=96),
                axes=AXIS_ORDER,
                axis_definitions=axis_definitions_for(AXIS_ORDER),
            ),
            fit=BoardFitV1_1(
                metric="cosine",
                k=2,
                k_cap=5,
                cluster_cut=0.35,
                dup_cut=0.05,
                min_category_size=5,
                interval_level=0.9,
                far_outlier_iqr_multiplier=1.5,
                far_outlier_iqr_multiplier_source="eval/thresholds.json#/abstention/far_outlier",
                interval=IntervalMethod(method="loo-jackknife-plus", replicates=None, seed=0),
            ),
            categories=(Category(category_id="c0", n_local=3, member_ids=("r0", "r1", "r2")),),
        ),
        "board_stats": BoardStats(
            tightness=Tightness(
                loo_mean=0.5,
                loo_sd=0.1,
                loo_quantiles={"p10": 0.25, "p50": 0.5, "p90": 0.75},
            ),
            leverage=(Leverage(reference_id="r2", delta_tightness=0.1, rank=1),),
            flags=(),
        ),
        "references": tuple(_reference(i) for i in range(3)),
        "assets": (_scored_asset(), _abstained_asset()),
        "comparisons": Comparisons(ties=(), note="No pairs were compared."),
        "provenance": ProvenanceV1_1(
            engine=EngineProvenanceV1_1(
                name="moodboard",
                version="0.1.0",
                source=EngineSourceProvenance(
                    source_repository="https://github.com/ohdearquant/moodboard.git",
                    source_revision="a" * 40,
                    source_dirty=True,
                ),
            ),
            model=ModelProvenance(repo="local/classical", revision="2", sha256="no-checkpoint"),
            command=shlex.join(argv),
            argv=argv,
            seed=0,
            created_at="2026-08-08T14:00:01Z",
            schema=SchemaProvenance(
                id="https://github.com/ohdearquant/moodboard/schema/report_v1_1.schema.json",
                sha256=report_schema_sha256(SCHEMA_PATH_V1_1),
            ),
        ),
    }
    fields.update(overrides)
    return ReportV1_1(**fields)  # type: ignore[arg-type]


def _candidate_inputs(report: ReportV1_1) -> tuple[CandidateImageInput, ...]:
    return tuple(
        CandidateImageInput(
            asset_id=asset.asset_id,
            content_sha256=asset.image.content_sha256,
            mime=asset.image.mime,
            width=asset.image.width,
            height=asset.image.height,
        )
        for asset in report.assets
    )


def _validate_document(document: dict[str, object]) -> None:
    schema = json.loads(SCHEMA_PATH_V1_1.read_text(encoding="utf-8"))
    jsonschema.validate(document, schema, cls=jsonschema.Draft202012Validator)


def test_report_v1_1_schema_and_typed_round_trip_are_live():
    report = _report()
    document = to_json_dict(report)

    assert SCHEMA_PATH.name == "report_v1_0.schema.json"
    assert document["schema_version"] == "1.1"
    assert document["board"]["fit"]["k_cap"] == 5
    assert document["board"]["representation"]["axis_definitions"][0]["axis_id"] == "style"
    assert document["assets"][0]["image"]["content_sha256"] == _sha256("candidate-0")
    assert document["provenance"]["argv"] == list(report.provenance.argv)
    _validate_document(document)
    validate_report(report)
    assert from_json_dict(document) == report


def test_v1_0_schema_stays_closed_and_refuses_a_v1_1_writer_document():
    document = to_json_dict(_report())
    legacy_schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(document, legacy_schema, cls=jsonschema.Draft202012Validator)


@pytest.mark.parametrize("version", ["1.2", "2.0", "01.1", "garbage", None])
def test_version_dispatch_refuses_unknown_or_malformed_versions_before_content(
    version: str | None,
):
    document: dict[str, object] = {
        "schema_version": version,
        "assets": [{"state": "scored", "score": "poison"}],
    }

    with pytest.raises(UnsupportedSchemaVersionError, match="unsupported_schema_version"):
        from_json_dict(document)


def test_v1_1_writer_requires_original_candidate_input_evidence(tmp_path):
    report = _report()
    path = tmp_path / "report.json"

    with pytest.raises(ValueError, match="candidate input evidence"):
        write_report(report, path)
    assert not path.exists()

    write_report(report, path, candidate_inputs=_candidate_inputs(report))
    assert from_json_dict(json.loads(path.read_text(encoding="utf-8"))) == report


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content_sha256", _sha256("substituted-candidate")),
        ("mime", "image/webp"),
        ("width", 31),
        ("height", 21),
    ],
)
def test_writer_rejects_candidate_identity_that_disagrees_with_input_evidence(
    tmp_path, field: str, value: object
):
    report = _report()
    corrupted = replace(report.assets[0].image, **{field: value})
    mutated = replace(
        report,
        assets=(replace(report.assets[0], image=corrupted), report.assets[1]),
    )
    path = tmp_path / "report.json"
    path.write_text("existing-good-output\n", encoding="utf-8")

    with pytest.raises(ValueError, match=rf"a0.*{field}"):
        write_report(mutated, path, candidate_inputs=_candidate_inputs(report))
    assert path.read_text(encoding="utf-8") == "existing-good-output\n"


@pytest.mark.parametrize("state_index", [0, 1])
def test_both_asset_states_require_candidate_image(state_index: int):
    document = to_json_dict(_report())
    del document["assets"][state_index]["image"]

    with pytest.raises(jsonschema.ValidationError):
        _validate_document(document)


def test_fit_policy_is_complete_and_closed():
    document = to_json_dict(_report())
    expected = {
        "metric",
        "k",
        "k_cap",
        "cluster_cut",
        "dup_cut",
        "min_category_size",
        "interval_level",
        "far_outlier_iqr_multiplier",
        "far_outlier_iqr_multiplier_source",
        "interval",
    }
    assert set(document["board"]["fit"]) == expected

    for key in sorted(expected - {"interval"}):
        mutated = copy.deepcopy(document)
        del mutated["board"]["fit"][key]
        with pytest.raises(jsonschema.ValidationError):
            _validate_document(mutated)

    mutated = copy.deepcopy(document)
    mutated["board"]["fit"]["registry_override"] = True
    with pytest.raises(jsonschema.ValidationError):
        _validate_document(mutated)


def test_engine_source_provenance_is_all_or_none_in_the_wire_shape():
    document = to_json_dict(_report())
    _validate_document(document)
    del document["provenance"]["engine"]["source_repository"]

    with pytest.raises(jsonschema.ValidationError):
        _validate_document(document)

    report = _report()
    without_source = replace(
        report,
        provenance=replace(
            report.provenance,
            engine=replace(report.provenance.engine, source=None),
        ),
    )
    validate_report(without_source)


def test_command_must_equal_the_posix_join_of_argv():
    report = _report()
    mutated = replace(
        report,
        provenance=replace(report.provenance, command="moodboard rank different-input"),
    )

    with pytest.raises(ValueError, match="shlex.join"):
        validate_report(mutated)


def test_schema_hash_must_identify_the_exact_schema_bytes():
    assert report_schema_sha256(SCHEMA_PATH_V1_1) == (
        "5eb8a20e865612676e89f2cd58ab17108c715102e66d66680a5ceb1cd7626ed8"
    )
    report = _report()
    mutated = replace(
        report,
        provenance=replace(
            report.provenance,
            schema=replace(report.provenance.schema, sha256="0" * 64),
        ),
    )

    with pytest.raises(ValueError, match="schema.*sha256"):
        validate_report(mutated)


def test_schema_provenance_id_must_equal_the_schema_file_id(tmp_path):
    schema = json.loads(SCHEMA_PATH_V1_1.read_text(encoding="utf-8"))
    schema["$id"] = "https://example.invalid/substituted-schema.json"
    schema_path = tmp_path / "substituted-schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    report = _report()
    mutated = replace(
        report,
        provenance=replace(
            report.provenance,
            schema=replace(
                report.provenance.schema,
                sha256=report_schema_sha256(schema_path),
            ),
        ),
    )

    with pytest.raises(ValueError, match=r"schema.*\$id"):
        validate_report(mutated, schema_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mime", "image/jpeg"),
        ("width", 4),
        ("height", 3),
        ("data_base64", base64.b64encode(b"not an image").decode("ascii")),
    ],
)
def test_thumbnail_mime_dimensions_and_bytes_are_verified(field: str, value: object):
    report = _report()
    changed_thumbnail = replace(report.assets[0].image.thumbnail, **{field: value})
    changed_image = replace(report.assets[0].image, thumbnail=changed_thumbnail)
    mutated = replace(
        report,
        assets=(replace(report.assets[0], image=changed_image), report.assets[1]),
    )

    with pytest.raises(ValueError, match="thumbnail"):
        validate_report(mutated)


def test_reference_thumbnails_receive_the_same_strict_decode_validation():
    report = _report()
    corrupted = replace(
        report.references[0].thumbnail, data_base64=base64.b64encode(b"<svg/>").decode()
    )
    mutated = replace(
        report,
        references=(replace(report.references[0], thumbnail=corrupted), *report.references[1:]),
    )

    with pytest.raises(ValueError, match=r"references\[0\].*thumbnail"):
        validate_report(mutated)


@pytest.mark.parametrize(
    "exemplars",
    [
        _exemplars()[:2],
        _exemplars() + (Exemplar(reference_id="r0", similarity=0.1),),
        (
            Exemplar(reference_id="r2", similarity=0.9),
            Exemplar(reference_id="r2", similarity=0.8),
            Exemplar(reference_id="r1", similarity=0.7),
        ),
        (
            Exemplar(reference_id="missing", similarity=0.9),
            Exemplar(reference_id="r0", similarity=0.8),
            Exemplar(reference_id="r1", similarity=0.7),
        ),
        tuple(reversed(_exemplars())),
    ],
)
def test_exemplars_have_exact_cardinality_uniqueness_resolution_and_order(
    exemplars: tuple[Exemplar, ...],
):
    report = _report()
    mutated = replace(
        report,
        assets=(replace(report.assets[0], exemplars=exemplars), report.assets[1]),
    )

    with pytest.raises(ValueError, match="exemplar"):
        validate_report(mutated)


def test_equal_similarity_exemplars_follow_reference_catalogue_order():
    report = _report()
    wrong = (
        Exemplar(reference_id="r1", similarity=0.8),
        Exemplar(reference_id="r0", similarity=0.8),
        Exemplar(reference_id="r2", similarity=0.7),
    )
    mutated = replace(
        report,
        assets=(replace(report.assets[0], exemplars=wrong), report.assets[1]),
    )

    with pytest.raises(ValueError, match="catalogue order"):
        validate_report(mutated)


def test_two_reference_board_requires_and_accepts_exactly_two_exemplars():
    report = _report()
    exemplars = (
        Exemplar(reference_id="r0", similarity=0.8),
        Exemplar(reference_id="r1", similarity=0.7),
    )
    mutated = replace(
        report,
        board=replace(
            report.board,
            n_references=2,
            n_eff=2.0,
            fit=replace(report.board.fit, k=1),
            categories=(Category(category_id="c0", n_local=2, member_ids=("r0", "r1")),),
        ),
        references=report.references[:2],
        assets=tuple(replace(asset, exemplars=exemplars) for asset in report.assets),
    )

    validate_report(mutated)


def test_reference_identifiers_are_unique_in_v1_1():
    report = _report()
    mutated = replace(
        report,
        references=(
            report.references[0],
            replace(report.references[1], reference_id="r0"),
            report.references[2],
        ),
    )

    with pytest.raises(ValueError, match="reference_id values must be unique"):
        validate_report(mutated)


def test_axis_definitions_are_exact_ordered_and_closed():
    definitions = axis_definitions_for(AXIS_ORDER)
    assert all(isinstance(definition, AxisDefinition) for definition in definitions)
    assert [definition.axis_id for definition in definitions] == ["style", *AXIS_ORDER]

    report = _report()
    reordered = (definitions[0], definitions[2], definitions[1], definitions[3])
    mutated = replace(
        report,
        board=replace(
            report.board,
            representation=replace(
                report.board.representation,
                axis_definitions=reordered,
            ),
        ),
    )
    with pytest.raises(ValueError, match="axis_definitions"):
        validate_report(mutated)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("label", "Palette drift"),
        ("value_kind", "conformal_p_value"),
        ("direction", "higher_is_better_fit"),
        ("aggregation", "full_conformal_category"),
        ("availability", "scored_only"),
        ("uncertainty", "asset_interval"),
        ("method", replace(axis_definitions_for(AXIS_ORDER)[1].method, revision=2)),
    ],
)
def test_each_axis_definition_value_is_governed(field: str, value: object):
    report = _report()
    definitions = list(report.board.representation.axis_definitions)
    definitions[1] = replace(definitions[1], **{field: value})
    mutated = replace(
        report,
        board=replace(
            report.board,
            representation=replace(
                report.board.representation,
                axis_definitions=tuple(definitions),
            ),
        ),
    )

    with pytest.raises(ValueError, match="axis_definitions"):
        validate_report(mutated)


def test_scored_style_equals_score_and_classical_values_are_always_numeric():
    report = _report()
    drifted = replace(
        report,
        assets=(
            replace(report.assets[0], axes={**report.assets[0].axes, "style": 0.5}),
            report.assets[1],
        ),
    )
    with pytest.raises(ValueError, match="style.*score"):
        validate_report(drifted)

    null_classical = replace(
        report,
        assets=(
            report.assets[0],
            replace(report.assets[1], axes={**report.assets[1].axes, "palette": None}),
        ),
    )
    with pytest.raises((TypeError, jsonschema.ValidationError, ValueError)):
        validate_report(null_classical)


def test_asset_interval_level_is_the_immutable_fit_level():
    report = _report()
    mutated = replace(
        report,
        assets=(
            replace(report.assets[0], interval=replace(report.assets[0].interval, level=0.8)),
            report.assets[1],
        ),
    )

    with pytest.raises(ValueError, match="interval level"):
        validate_report(mutated)


@pytest.mark.parametrize(
    ("state_index", "field", "value"),
    [(0, "reason", "resolution"), (1, "score", 0.5)],
)
def test_scored_and_abstained_wire_branches_remain_disjoint(
    state_index: int, field: str, value: object
):
    document = to_json_dict(_report())
    document["assets"][state_index][field] = value

    with pytest.raises(jsonschema.ValidationError):
        _validate_document(document)


@pytest.mark.parametrize(
    ("field", "value"),
    [("source_repository", "relative/path"), ("source_revision", "not-a-revision")],
)
def test_engine_source_provenance_rejects_relative_or_unpinned_identity(field: str, value: object):
    report = _report()
    assert report.provenance.engine.source is not None
    source = replace(report.provenance.engine.source, **{field: value})
    mutated = replace(
        report,
        provenance=replace(
            report.provenance,
            engine=replace(report.provenance.engine, source=source),
        ),
    )

    with pytest.raises((ValueError, jsonschema.ValidationError)):
        validate_report(mutated)


def test_unknown_root_key_is_rejected_by_the_closed_v1_1_schema():
    document = to_json_dict(_report())
    document["future"] = {}

    with pytest.raises(jsonschema.ValidationError):
        _validate_document(document)
