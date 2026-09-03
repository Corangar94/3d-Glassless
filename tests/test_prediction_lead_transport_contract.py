from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_writer_computes_lead_before_setting_validity_flag():
    source = _source("tracker/pose_shared_memory.py")
    write = source.split("    def write(self, pose:", 1)[1].split(
        "    def close(self)",
        1,
    )[0]

    publish = write.index("publish_timestamp_ms = int(")
    encoding = write.index("prediction_lead = _prediction_lead_encoding(")
    flags = write.index("flags = PoseFlags.VALID")
    validity = write.index("if prediction_lead.valid:")
    pack = write.index("prediction_lead.value_ms")

    assert publish < encoding < flags < validity < pack
    assert "flags |= PoseFlags.PREDICTION_LEAD_VALID" in write
    assert "| PoseFlags.PREDICTION_LEAD_VALID" not in write


def test_reader_clears_claimed_validity_when_value_is_invalid():
    source = _source("tracker/pose_shared_memory.py")
    read = source.split("    def read(self) -> PosePacketV2 | None:", 1)[1].split(
        "    def close(self)",
        1,
    )[0]

    sanitize = read.index("prediction_lead = sanitize_prediction_lead(")
    clear = read.index("if not prediction_lead.valid:")
    packet = read.index("return PosePacketV2(")

    assert sanitize < clear < packet
    assert "~int(PoseFlags.PREDICTION_LEAD_VALID)" in read
    assert "prediction_lead_ms=prediction_lead.value_ms" in read


def test_transport_layout_and_flag_bit_are_unchanged():
    source = _source("tracker/pose_shared_memory.py")
    overlay = _source("overlay/overlay.cpp")

    assert 'POSE_V2_FORMAT = "<IIffffffffffIIII"' in source
    assert "PREDICTION_LEAD_VALID = 1 << 3" in source
    assert "static_assert(sizeof(PoseV2) == 64" in overlay
    assert "POSE_V2_PREDICTION_LEAD_VALID = 1u << 3" in overlay


def test_signed_native_prediction_requires_declared_lead_validity():
    overlay = _source("overlay/overlay.cpp")
    prediction = overlay.split(
        "const bool predictionLeadKnown =",
        1,
    )[1].split("nativePrediction", 1)[0]

    assert "POSE_V2_PREDICTION_LEAD_VALID" in prediction
    assert "poseFresh && predictionLeadKnown" in prediction


def test_value_only_helper_remains_compatible():
    source = _source("tracker/pose_shared_memory.py")

    assert "def _prediction_lead_ms(" in source
    assert "return _prediction_lead_encoding(" in source
    assert ").value_ms" in source
