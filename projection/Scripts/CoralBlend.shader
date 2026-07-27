// Two-layer cross-fade for the coral projection.
//
// With Project Settings > Player > Color Space set to Linear and the source
// RenderTextures created with RenderTextureReadWrite.sRGB, tex2D returns
// linearised values — so this lerp is a linear-light blend. That matters here
// because the dissolve runs between the bright healthy look and the deliberately
// stopped-down bleached look: the same lerp in gamma space produces a midpoint
// darker than the true half-exposure, and the long state 0/1 fade visibly sags
// through the midtones.
//
// Fully opaque, no depth, no culling — it only ever runs as a full-screen blit.
Shader "Coral/Blend2"
{
    Properties
    {
        _MainTex ("Base (unused, required by Blit)", 2D) = "black" {}
        _TexA ("Layer A", 2D) = "black" {}
        _TexB ("Layer B", 2D) = "black" {}
        _Blend ("A to B", Range(0, 1)) = 0
        _UvRect ("Source window (xy = scale, zw = offset)", Vector) = (1, 1, 0, 0)
    }

    SubShader
    {
        Cull Off
        ZWrite Off
        ZTest Always

        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment frag
            #include "UnityCG.cginc"

            sampler2D _TexA;
            sampler2D _TexB;
            float _Blend;
            float4 _UvRect;   // xy = scale, zw = offset — maps canvas UV to source UV

            fixed4 frag(v2f_img i) : SV_Target
            {
                // Aspect fit. Both layers share it: every clip is the same size.
                float2 uv = i.uv * _UvRect.xy + _UvRect.zw;

                // Only "contain" samples outside the source. Return black rather
                // than letting the sampler smear its edge pixels down the bars.
                if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0)
                    return float4(0.0, 0.0, 0.0, 1.0);

                float3 a = tex2D(_TexA, uv).rgb;
                float3 b = tex2D(_TexB, uv).rgb;
                return float4(lerp(a, b, _Blend), 1.0);
            }
            ENDCG
        }
    }

    FallBack Off
}
