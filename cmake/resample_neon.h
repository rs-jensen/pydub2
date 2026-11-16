// resample_sse.h of libopusenc 0.3 for arm64, the same two functions with neon, see THIRD_PARTY.md
#ifdef __aarch64__
#include <arm_neon.h>

#define OVERRIDE_INNER_PRODUCT_SINGLE
static inline float inner_product_single(const float *a, const float *b, unsigned int len)
{
   unsigned int i;
   float32x4_t s0 = vdupq_n_f32(0), s1 = vdupq_n_f32(0);
   for (i=0;i<len;i+=8)
   {
      s0 = vfmaq_f32(s0, vld1q_f32(a+i), vld1q_f32(b+i));
      s1 = vfmaq_f32(s1, vld1q_f32(a+i+4), vld1q_f32(b+i+4));
   }
   return vaddvq_f32(vaddq_f32(s0, s1));
}

#define OVERRIDE_INTERPOLATE_PRODUCT_SINGLE
static inline float interpolate_product_single(const float *a, const float *b, unsigned int len, const spx_uint32_t oversample, float *frac)
{
   unsigned int i;
   float32x4_t s0 = vdupq_n_f32(0), s1 = vdupq_n_f32(0), s2 = vdupq_n_f32(0), s3 = vdupq_n_f32(0);
   for (i=0;i<len;i+=4)
   {
      s0 = vfmaq_n_f32(s0, vld1q_f32(b+i*oversample), a[i]);
      s1 = vfmaq_n_f32(s1, vld1q_f32(b+(i+1)*oversample), a[i+1]);
      s2 = vfmaq_n_f32(s2, vld1q_f32(b+(i+2)*oversample), a[i+2]);
      s3 = vfmaq_n_f32(s3, vld1q_f32(b+(i+3)*oversample), a[i+3]);
   }
   return vaddvq_f32(vmulq_f32(vld1q_f32(frac), vaddq_f32(vaddq_f32(s0, s1), vaddq_f32(s2, s3))));
}
#endif
