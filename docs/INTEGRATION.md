# 内核模块集成说明

本文记录 Overclock 应用与 `maixcam_cpu` / `maixcam_npu` 两个工具的集成方式，以及模块的来源、校验信息和重新编译方法。

---

## 1. 模块来源

| 应用内路径 | 来源 | 大小 | SHA-256 |
|---|---|---|---|
| `assets/modules/mpll_freq.ko` | `D:\Project\maixcam_cpu\prebuilt\mpll_freq.ko` | 10136 B | `99E75FB5C993570FD02A3DF7AF60EF04EFBFD473FB54CE6F57CF1DDC939BF369` |
| `assets/modules/npu_freq.ko` | `D:\Project\maixcam_npu\prebuilt\npu_freq.ko` | 11376 B | `91C41A4A833C3E0BA1BD3F4C2FFAD65BA2F9EBE3914296ECE65B9077126E47E3` |

GPL 源码备份在本目录的 `vendor/cpu/`、`vendor/npu/` 下，和应用内的二进制模块版本一致：

- `vendor/cpu/mpll_freq.c` — SPDX GPL-2.0
- `vendor/npu/npu_freq.c` — SPDX GPL-2.0

两个 `.ko` 的 MD5 也与设备 `/root/cpu_tool/`、`/root/npu_tool/` 中的已验证版本一致：

```text
c26319d49900d9c1aff456c12538abd4  mpll_freq.ko
59d7911ada68d9cf8a0acfd93f6d73b3  npu_freq.ko
```

目标内核：

```text
Linux 5.10.4-tag- #14 PREEMPT riscv64
vermagic: 5.10.4-tag- preempt mod_unload riscv64
```

---

## 2. 接口

### CPU 模块

```sh
insmod assets/modules/mpll_freq.ko target=<hz> delay_ms=0
```

- `target`：目标 `clk_mpll` 频率（Hz），必须为 25 MHz 的整数倍；
- 实际改频在 `delayed_work` 中执行，所以 `insmod` 会先返回；
- 应用轮询 `/sys/kernel/debug/clk/clk_c906_0/clk_rate` 校验，随后 `rmmod mpll_freq`。

### NPU 模块

```sh
insmod assets/modules/npu_freq.ko freq=<hz> delay_ms=0
```

- `freq`：目标 NPU 频率（Hz），模块自动按当前 `clk_tpll / clk_tpu` 分频比换算 PLL 目标；
- 应用在改频前检查 `/sys/kernel/debug/clk/clk_tpu/clk_enable_count == 0`；
- 应用轮询 `/sys/kernel/debug/clk/clk_tpu/clk_rate` 校验，随后 `rmmod npu_freq`。

两个模块都只在 Apply / Reset 期间短暂加载，成功后立刻卸载；频率由 PLL 保持，不依赖模块常驻。

---

## 3. 为什么这样调用

- 内核没有 `cpufreq` / DVFS，MaixPy 只提供只读的 `cpu_freq()` / `npu_freq()`；
- 直接 `devmem` 改分频位不会让厂商 PLL 驱动重算 `ictrl`，频率不会真正改变；
- 走模块的 `clk_set_rate()` 才会调用厂商驱动，重算 PLL 参数并等待重新锁定；
- 通过 debugfs 的 `clk_rate` 做闭环校验，比只看 `insmod` 返回值可靠。

---

## 4. 重新编译

需要 Linux（WSL2 也可以）+ 目标内核树（`EXTRAVERSION=-tag-`）+ 设备 `.config` + `riscv64-linux-gnu-gcc`。

### CPU

```sh
cd vendor/cpu
make ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- \
     -C /path/to/linux-5.10.4 M=$PWD modules
```

产物为 `mpll_freq.ko`，复制到 `assets/modules/`。

### NPU

```sh
cd vendor/npu
make ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- \
     -C /path/to/linux-5.10.4 M=$PWD modules
```

产物为 `npu_freq.ko`，复制到 `assets/modules/`。

编译后务必检查：

```sh
modinfo mpll_freq.ko | grep vermagic
modinfo npu_freq.ko | grep vermagic
# 5.10.4-tag- preempt mod_unload riscv64
```

> 设备上没有 `/lib/modules`，所以不能用 `modprobe`，应用直接 `insmod /绝对路径/xxx.ko`。

---

## 5. 读取节点

| 用途 | 路径 |
|---|---|
| CPU 实际频率 | `/sys/kernel/debug/clk/clk_c906_0/clk_rate` |
| NPU 实际频率 | `/sys/kernel/debug/clk/clk_tpu/clk_rate` |
| NPU 占用状态 | `/sys/kernel/debug/clk/clk_tpu/clk_enable_count` |
| 芯片温度 | `/sys/class/thermal/thermal_zone0/temp`（毫摄氏度） |

---

## 6. 安全约束

- 只改 `clk_mpll` / `clk_tpll`，不动 DDR/显示/视频/USB PLL；
- NPU 改频前要求 enable_count 为 0；
- 目标频率严格限制在 UI 可选项：CPU 850–1000 / 25 MHz，NPU 700/750/800；
- 每次改频前后读回频率；失败时尝试卸载模块并给出内核日志；
- 无电压联动。高频稳定性需要自行评估，异常时断电重启即可恢复原厂时钟。
