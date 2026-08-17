/* SPDX-License-Identifier: CC0-1.0
 * Minimal official ReShade shader helper subset used by Glassless3D.
 * Based on crosire/reshade-shaders Shaders/ReShade.fxh.
 */
#pragma once

#if !defined(__RESHADE__) || __RESHADE__ < 30000
#error "ReShade 3.0+ is required"
#endif

#ifndef RESHADE_DEPTH_INPUT_IS_UPSIDE_DOWN
#define RESHADE_DEPTH_INPUT_IS_UPSIDE_DOWN 0
#endif
#ifndef RESHADE_DEPTH_INPUT_IS_REVERSED
#define RESHADE_DEPTH_INPUT_IS_REVERSED 1
#endif
#ifndef RESHADE_DEPTH_INPUT_IS_MIRRORED
#define RESHADE_DEPTH_INPUT_IS_MIRRORED 0
#endif
#ifndef RESHADE_DEPTH_INPUT_IS_LOGARITHMIC
#define RESHADE_DEPTH_INPUT_IS_LOGARITHMIC 0
#endif
#ifndef RESHADE_DEPTH_MULTIPLIER
#define RESHADE_DEPTH_MULTIPLIER 1
#endif
#ifndef RESHADE_DEPTH_LINEARIZATION_FAR_PLANE
#define RESHADE_DEPTH_LINEARIZATION_FAR_PLANE 1000.0
#endif

namespace ReShade
{
    texture BackBufferTex : COLOR;
    texture DepthBufferTex : DEPTH;
    sampler BackBuffer { Texture = BackBufferTex; };
    sampler DepthBuffer { Texture = DepthBufferTex; };

    float GetLinearizedDepth(float2 texcoord)
    {
#if RESHADE_DEPTH_INPUT_IS_UPSIDE_DOWN
        texcoord.y = 1.0 - texcoord.y;
#endif
#if RESHADE_DEPTH_INPUT_IS_MIRRORED
        texcoord.x = 1.0 - texcoord.x;
#endif
        float depth = tex2Dlod(DepthBuffer, float4(texcoord, 0, 0)).x * RESHADE_DEPTH_MULTIPLIER;
#if RESHADE_DEPTH_INPUT_IS_LOGARITHMIC
        const float C = 0.01;
        depth = (exp(depth * log(C + 1.0)) - 1.0) / C;
#endif
#if RESHADE_DEPTH_INPUT_IS_REVERSED
        depth = 1.0 - depth;
#endif
        const float N = 1.0;
        depth /= RESHADE_DEPTH_LINEARIZATION_FAR_PLANE -
            depth * (RESHADE_DEPTH_LINEARIZATION_FAR_PLANE - N);
        return depth;
    }
}

void PostProcessVS(in uint id : SV_VertexID, out float4 position : SV_Position,
                   out float2 texcoord : TEXCOORD)
{
    texcoord.x = (id == 2) ? 2.0 : 0.0;
    texcoord.y = (id == 1) ? 2.0 : 0.0;
    position = float4(texcoord * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
}
