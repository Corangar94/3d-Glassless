from launcher.game_profiles import (
    Backend,
    GameProfile,
    PlayContext,
    RequestedMode,
    evaluate_profile,
)


def test_online_profile_fails_closed_to_non_injecting_desktop():
    profile = GameProfile(
        profile_id="arena",
        display_name="Arena",
        executable_path="C:/Games/Arena/Arena.exe",
        play_context=PlayContext.ONLINE_MULTIPLAYER,
        requested_mode=RequestedMode.OFFLINE_ADVANCED,
        advanced_acknowledged=True,
    )

    decision = evaluate_profile(profile)

    assert decision.active_mode is RequestedMode.NON_INJECTING_DESKTOP
    assert decision.allows(Backend.DESKTOP_OVERLAY)
    assert decision.allows(Backend.WINDOWS_GRAPHICS_CAPTURE)
    assert not decision.allows(Backend.RESHADE_ADDON)
    assert decision.reason == "online profiles permit non-injecting desktop only"


def test_offline_advanced_requires_acknowledgement():
    profile = GameProfile(
        profile_id="story",
        display_name="Story",
        executable_path="C:/Games/Story/Story.exe",
        play_context=PlayContext.OFFLINE_SINGLEPLAYER,
        requested_mode=RequestedMode.OFFLINE_ADVANCED,
        advanced_acknowledged=False,
    )

    decision = evaluate_profile(profile)

    assert decision.active_mode is RequestedMode.NON_INJECTING_DESKTOP
    assert not decision.allows(Backend.RESHADE_ADDON)
    assert decision.reason == "offline advanced requires acknowledgement"


def test_acknowledged_offline_profile_allows_reshade_only():
    profile = GameProfile(
        profile_id="story",
        display_name="Story",
        executable_path="C:/Games/Story/Story.exe",
        play_context=PlayContext.OFFLINE_SINGLEPLAYER,
        requested_mode=RequestedMode.OFFLINE_ADVANCED,
        advanced_acknowledged=True,
    )

    decision = evaluate_profile(profile)

    assert decision.active_mode is RequestedMode.OFFLINE_ADVANCED
    assert decision.allows(Backend.DESKTOP_OVERLAY)
    assert decision.allows(Backend.RESHADE_ADDON)


def test_publisher_approved_mode_remains_reserved_and_fails_closed():
    profile = GameProfile(
        profile_id="approved",
        display_name="Approved",
        executable_path="C:/Games/Approved/Approved.exe",
        play_context=PlayContext.OFFLINE_SINGLEPLAYER,
        requested_mode=RequestedMode.PUBLISHER_APPROVED_INTEGRATION,
        approval_id="publisher/example/1.2.3",
    )

    decision = evaluate_profile(profile)

    assert decision.active_mode is RequestedMode.NON_INJECTING_DESKTOP
    assert not decision.allows(Backend.RESHADE_ADDON)
    assert decision.reason == "publisher-approved integration is not implemented"
