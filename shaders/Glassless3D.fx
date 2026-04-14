// shaders/Glassless3D.fx
// Glassless3D — head-tracked depth parallax for ReShade 5.9+
// Requires Glassless3D.addon (reads FT_SharedMem) running alongside.

#include "ReShade.fxh"
#include "Glassless3D.fxh"

// ── Uniforms set by Glassless3D.addon each frame ───────────────────────────
// Defaults produce a no-op effect when the addon / tracker is not running.
uniform float g3d_HeadX < source = "g3d_HeadX"; > = 0.0;
uniform float g3d_HeadY < source = "g3d_HeadY"; > = 0.0;
uniform float g3d_HeadZ < source = "g3d_HeadZ"; > = 60.0;

// ── User-tunable parameters (ReShade UI overlay) ──────────────────────────
uniform float ScreenWidthCM <
    ui_category = "Screen Setup";
    ui_type     = "slider";
    ui_label    = "Screen Width (cm)";
    ui_tooltip  = "Measure your monitor with a ruler.";
    ui_min = 20.0; ui_max = 120.0; ui_step = 0.5;
> = 59.8;

uniform float ScreenHeightCM <
    ui_category = "Screen Setup";
    ui_type     = "slider";
    ui_label    = "Screen Height (cm)";
    ui_min = 10.0; ui_max = 70.0; ui_step = 0.5;
> = 33.6;

uniform float ConvergenceDist <
    ui_category = "3D Effect";
    ui_type     = "slider";
    ui_label    = "Convergence Distance (0-1)";
    ui_tooltip  = "Depth plane that sits on the screen. "
                  "0=near, 1=far. Start at 0.5, adjust until "
                  "mid-scene objects feel flat.";
    ui_min = 0.01; ui_max = 0.99; ui_step = 0.01;
> = 0.50;

uniform float EffectStrength <
    ui_category = "3D Effect";
    ui_type     = "slider";
    ui_label    = "Effect Strength";
    ui_tooltip  = "Increase for more 3D. Reduce if ghosting appears.";
    ui_min = 0.0; ui_max = 1.0; ui_step = 0.01;
> = 0.30;

uniform bool ShowDepthBuffer <
    ui_category = "Debug";
    ui_label    = "Show Depth Buffer";
    ui_tooltip  = "Greyscale depth view. Dark=near, white=far. "
                  "If all one colour, depth isn't accessible.";
> = false;

// ── Pixel shader ─────────────────────────────────────────────────────────
float4 PS_Glassless3D(float4 pos : SV_Position, float2 uv : TEXCOORD) : SV_Target
{
    float depth = ReShade::GetLinearizedDepth(uv);

    if (ShowDepthBuffer)
        return float4(depth, depth, depth, 1.0);

    // Convert head cm → UV-space fraction; flip Y (image Y grows down, world Y up)
    float2 head_uv = float2(
         g3d_HeadX / max(ScreenWidthCM,  1.0),
        -g3d_HeadY / max(ScreenHeightCM, 1.0)
    );

    float2 offset    = G3D_ParallaxOffset(head_uv, depth, ConvergenceDist, EffectStrength);
    float2 sampleUV  = saturate(uv + offset);

    return tex2D(ReShade::BackBuffer, sampleUV);
}

// ── Technique ─────────────────────────────────────────────────────────────
technique Glassless3D <
    ui_label   = "Glassless3D";
    ui_tooltip = "Head-tracked parallax 3D effect. "
                 "Requires Glassless3D.addon + tracker (or OpenTrack) running.";
>
{
    pass
    {
        VertexShader = PostProcessVS;
        PixelShader  = PS_Glassless3D;
    }
}
