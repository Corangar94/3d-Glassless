from tracker.target_displays import inventory_text_is_known_target


def test_target_display_matcher_accepts_compact_known_target_tokens():
    assert inventory_text_is_known_target("Generic PnP Monitor DISPLAY\\LEIASR\\UID0 LeiaSR")
    assert inventory_text_is_known_target("Generic PnP Monitor DISPLAY\\LUMEPAD2\\UID0 LumePad2")
    assert inventory_text_is_known_target("Generic PnP Monitor DISPLAY\\THINKVISION27-3D\\UID0")
    assert inventory_text_is_known_target("Generic PnP Monitor DISPLAY\\SIMULATEDREALITY\\UID0")


def test_target_display_matcher_rejects_broad_vendor_or_generic_monitor_tokens():
    assert not inventory_text_is_known_target("Generic PnP Monitor DISPLAY\\SAM71AC\\UID4352 SAM")
    assert not inventory_text_is_known_target("Generic PnP Monitor DISPLAY\\ACR1234\\UID0 Acer")
    assert not inventory_text_is_known_target("Generic PnP Monitor DISPLAY\\LEIA\\UID0 Leia")
