#include <stdio.h>

#include <libplacebo/config.h>
#include <libplacebo/utils/libav.h>

int main(void)
{
    struct pl_plane_data data[4];
    struct pl_bit_encoding bits;

    printf("kerao-consumer version %s\n", pl_version());
    printf("kerao-consumer planes %d\n",
           pl_plane_data_from_pixfmt(data, &bits, AV_PIX_FMT_NV12));
    return 0;
}
