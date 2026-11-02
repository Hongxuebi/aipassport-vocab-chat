// 主机侧最小 esp_partition.h 桩：只提供 pet_anim.c 用到的符号
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

typedef enum { ESP_PARTITION_TYPE_APP = 0, ESP_PARTITION_TYPE_DATA = 1 } esp_partition_type_t;
typedef enum { ESP_PARTITION_MMAP_DATA = 0, ESP_PARTITION_MMAP_INSTR = 1 } esp_partition_mmap_memory_t;
typedef struct esp_partition_mmap_handle_s *esp_partition_mmap_handle_t;

typedef struct {
    esp_partition_type_t type;
    uint8_t   subtype;
    const char *label;
    uint32_t  address;
    uint32_t  size;
} esp_partition_t;

static inline const esp_partition_t *esp_partition_find_first(esp_partition_type_t t, uint8_t st,
                                                              const char *label)
{
    (void)t; (void)st; (void)label;
    return NULL;   // 主机上不挂分区：测试只走 pet_assets_from_blob
}

static inline esp_err_t esp_partition_mmap(const esp_partition_t *p, size_t off, size_t size,
                                           esp_partition_mmap_memory_t mem, const void **out,
                                           esp_partition_mmap_handle_t *h)
{
    (void)p; (void)off; (void)size; (void)mem; (void)out; (void)h;
    return ESP_FAIL;
}
