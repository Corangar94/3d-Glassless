// shaders/Glassless3D.fxh
// Shared math for Glassless3D.fx

// Compute the UV-space parallax offset for one pixel.
//   head_uv    : head X/Y normalised to UV fraction
//                 = float2(head_x_cm / screen_w_cm, -head_y_cm / screen_h_cm)
//   depth      : linearised depth [0,1] (0=near, 1=far)
//   convergence: depth plane that appears "on" the screen [0,1]
//   strength   : overall scale factor (0=off, 1=full)
float2 G3D_ParallaxOffset(
    float2 head_uv,
    float  depth,
    float  convergence,
    float  strength)
{
    // Objects at convergence → zero offset (appear on-screen).
    // Objects closer          → positive offset (pop toward viewer).
    // Objects further         → negative offset (recede).
    float factor = (1.0 - depth / max(convergence, 0.001)) * strength;
    return head_uv * factor;
}
