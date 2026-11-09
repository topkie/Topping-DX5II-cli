# dx5ii

[![PyPI](https://img.shields.io/pypi/v/dx5ii-cli.svg)](https://pypi.org/project/dx5ii-cli/)
[![Python](https://img.shields.io/pypi/pyversions/dx5ii-cli.svg)](https://pypi.org/project/dx5ii-cli/)
[![CI](https://github.com/topkie/dx5ii-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/topkie/dx5ii-cli/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[English](README.md) | **简体中文**

控制 Topping DX5 II（VID `0x152A` / PID `0x8750`，控制协议走 HID `MI_02`）的 Python 包与命令行工具：
读全部设备状态、按字段写设置、切换输出端口与 EQ 预设、管理设备里的 PEQ 预设。

协议没有公开文档，是从 Topping Home 的 Web 前端 bundle 逆出来的，再在实机上逐字段验证（固件版本 2.58）。
帧格式、命令号、字段映射见 `src/dx5ii/constants.py` 与 `src/dx5ii/protocol.py` 的模块注释。

DX5 II 的固件从 `V2.39` 开始支持 Web Home，cli 只在固件 2.58 上验证过，使用时确认固件版本 >= 2.39 或更新到 2.58。

## 安装

```bash
pip install dx5ii-cli
```

只依赖 `hidapi`。只要 Topping Web Home 能连接上设备即可，不需要官方应用在跑。

确认安装：
```bash
dx5ii --version        # dx5ii 版本
dx5ii --devices        # 列出 HID 接口，确认能看到 DX5 II
dx5ii --fast           # 读一遍状态
```
--devices 是最快的连通性检查：它不发送任何协议帧，只枚举 HID。

从源码安装：
```
git clone <repo> && cd dx5ii
pip install -e .
```

不安装也能用：

```bash
export PYTHONPATH=/path/to/dx5ii/src   # Windows PowerShell: $env:PYTHONPATH = '...\dx5ii\src'
python -m dx5ii get
```

## 命令行

```bash
dx5ii                                   # 读全状态（含 EQ 预设列表）
dx5ii --fast                            # 同上，跳过预设列表
dx5ii --json                            # 输出 JSON
dx5ii --devices                         # 枚举 HID 接口（含固件版本），标出协议口
dx5ii --set-output hp_balanced          # 只切输出端口，然后读回
dx5ii --set-config Dynamic              # 只切 EQ 预设（名字或索引）
dx5ii --set-output hp_balanced --set-config Dynamic
```

`dx5ii` 输出的第一行带设备固件版本，取自 USB `bcdDevice` 描述符（JSON 里的 `release_number`），
形如 `firmware=2.58`。

`--json` 以可读名字作为值，设备索引放在旁边的 `<key>_raw`
（`{"output": "hp_balanced", "output_raw": 4}`）；切换报告的 `before` / `after` 同样如此。
选项名字数组的掩码在 settings 块里（`output_option_mask`），布尔字段保持布尔。偏好数字的脚本读
`*_raw` 键即可，不需要从显示字符串里反解析。

`--set-config` 也能结束 Topping Home 的云端预览。云端配置在放时设备仍保留自己的存储槽位，所以
「索引与存储槽相同」并不代表它可听——命令照样会发，正是这一发把设备切回本地预设：

```
before   : output=hp_balanced  stored=2 (IER-M7.Harman)  playing=cloud
  config 2 -> 2  (left cloud preview)
after    : output=hp_balanced  stored=2 (IER-M7.Harman)  playing=local
```

脚本要注意三条：

- `changed` 表示设备状态确实变了。退出预览也算变化，所以即使 `from == to`，`changed` 也是
  `true`；变的是 `playing`，不是 `stored`。
- `left_preview` 只在结束预览时出现在 action 里，值为 `"cloud"` 或 `"audition"`；普通切槽没有这个键。
- 顶层 `changed` 遵循同一规则；`ok` 仍表示「切完后读回的值等于目标」。

只有「存储的是它、而且确实在放本地槽」时才跳过写。`0x7137` 没有回包时 `playing_source` 是
`"unknown"`，这时照样发命令。

字段读写：

```bash
dx5ii get                               # 全部字段，按组打印
dx5ii get output theme volume           # 只读指定字段
dx5ii set brightness medium             # 写一个字段，打印 before -> after
dx5ii set theme blue language en        # 一次写多个
dx5ii raw 0x7137 --pt read              # 发一帧原始命令，打印回包
dx5ii raw 0x710C --pt read --total 1    # 带 total/cur 的原始帧
dx5ii watch 5                           # 监听 5 秒内设备主动推送的帧
```

`raw` 打印的是设备回包里的**原始帧**：每条解出 `cmd` / `pt` / `total` / `cur` / `data`，不套用字段解释，也不做单位换算。`0x710C` 的回复是 52 行，`0x7137` 是三行，每一行是一个 32 位字；`data` 同时给出十进制与十六进制。自己发多帧命令时用 `--total` / `--cur` 指定总帧数与序号，设备只接受序号连续的帧。

可用字段共 43 个，`dx5ii --help` 会完整列出；分组如下：

| 组 | 字段 |
|---|---|
| 电源与主机 | `power` `power-trigger` `home-page` `theme` `brightness` `language` `screen-timeout` `dim-screen-type` `trigger-out` |
| 输入输出 | `input` `output` `input-option` `output-option` `input-mode` `spdif-mode` `headphone-gain` `balance` `polarity` |
| 音频 | `volume` `mute` `pcm-filter` `line-mode` `uac` `volume-step` `vu-bar-mode` `vu-level` `dc-detect` `crossfeed-type` `crossfeed-conv` `crossfeed-simple` `bluetooth` `aptx` `remote` |
| 记忆 | `volume-memory` `peq-memory` `crossfeed-memory` |
| 按键 | `multifunction-key` `double-key` `remote-a-key` `remote-b-key` `remote-mute-mode` |
| 设备 | `device-name`(仅读) `peq`(开关) |

采样率不在字段表里：它由 `0x710F` 单独上报，出现在 `dx5ii` / `dx5ii get` 的全量输出的 `sampling` 段。

`set` 支持的值形式：`on`/`off`、`true`/`false`、枚举名（如 `hp_balanced`、`aurora`）、
索引数字、掩码列表（如 `input-option usb,bt`）、音量 `-40.0`、超时 `off`/`10s`/`30s`/`60s`。

## EQ 预设

```bash
dx5ii configs                                    # 当前音源、存储槽位与全部预设
dx5ii peq list                                   # 列出预设（设备存储的当前项标 ←）
dx5ii peq show Dynamic                           # 打印左右声道每段的频点/增益/Q
dx5ii peq create MyPreset --copy Dynamic         # 复制一份新预设（默认不激活）
dx5ii peq create MyPreset --from-apo delta.txt   # 从 Equalizer APO 的 Filter 行导入
dx5ii peq create MyPreset --copy SPEAKER --activate
dx5ii peq rename MyPreset Better                 # 改名，名字最多 14 字节
dx5ii peq delete MyPreset --switch Dynamic       # 删除，并把激活项切到指定预设
```

`peq create` / `peq delete` 只做新建与删除，**不提供修改已有预设每一段的手段**——设备协议里没有
“改第 N 段”的单独命令，改一段就要整块重传，而重传等于覆盖，所以这里不做。

退出码：`0` 成功，`2` 设备/参数错误，`3` 写入后回读没变。

## 库用法

```python
from dx5ii import Dx5ii, apply_changes

with Dx5ii() as dev:
    state = dev.snapshot()                 # device/settings/sampling/peq/configs/pushed
    print(state["settings"]["volume_db"])

    dev.set_output("hp_balanced")          # 返回切换后的 settings
    dev.set_config_index(2)                # 0x110E，返回回读到的索引

with Dx5ii() as dev:                       # 一次切两样，拿到可回滚的 before
    snap = dev.snapshot()
    report = apply_changes(dev, snap, want_output="hp_balanced",
                           want_config="IER-M7.Harman")
    print(report["before"]["config_index"], report["after"]["config_index"])
```

PEQ：

```python
from dx5ii import Dx5ii, read_presets, create_preset, parse_apo_filters

with Dx5ii() as dev:
    presets = read_presets(dev)
    print(presets["stored_index"], [p["name"] for p in presets["presets"]])

    left = parse_apo_filters(open("delta.txt").read())
    create_preset(dev, "Imported", left)       # 只给左声道时右声道复制左侧
```

字段编码可以单独用：

```python
from dx5ii import encode_field

frames = encode_field("volume", -40.0, {"volume_step": "half_db"})
# [(28034, 80, 1, 1)]  -> cmd, data, total, cur
```

对外暴露的函数与方法都带类型标注，并随包发布 `py.typed`。

## 协议要点

- **帧**：16 字节 `22 33 <pt> <total> <cur> <cmdHi> <cmdLo> <data32 BE> <crcHi> <crcLo> 66 77 00`，
  hidapi 写入时前置一个 `0x00` 报告号。`pt` = `0x10` 读请求 / `0x20` 写请求，设备回包一律 `0x20`。
- **多帧**：`total` 是帧数，`cur` 是序号。`0x710C`（读设置）回 52 帧、`0x1120`（上传预设）回 393 帧，
  必须收齐才算一次完整读——只读一帧会看起来“设备没反应”。
- **EQ 预设块**：78 个 32 位字。`0..3` 名字（UTF-8，小端，NUL 填充）、`4` 启用标志、`5` 与 `7` 哈希、
  `8..40` 左声道 11 段 × 3 字、`41..73` 右声道、`74..77` 填充。
  每段：`packed = enabled | type<<8 | gain_tenth_db<<16`、`freq_hz`、`q*10000`；类型 `1=PK 4=LS 5=HS`。
- **上传必须是 74 帧**，不是 78：设备只收前 74 字。按 78 发，设备完全不理。

## 设计

| 模块 | 职责 |
|---|---|
| `constants.py` | 命令号、枚举表、settings 字布局 |
| `protocol.py` | 帧编解码、名字解码、音量换算 |
| `stream.py` | 多帧收集状态机：`curFrame == totalFrameLen - 1` 即收齐 |
| `device.py` | HID 句柄：打开、收发、读各命令、组装 snapshot |
| `interpreter.py` | 帧序列 → 状态 dict（纯函数，可离线测） |
| `fields.py` | 字段表、值编码、回读取值 |
| `bands.py` | PEQ 段的编解码、APO 文本解析 |
| `peq.py` | 预设的读/建/删/改名 |
| `actions.py` | “切端口 + 切预设”的高层动作与回滚信息 |
| `formatting.py` | 终端输出 |
| `cli.py` | argparse 子命令与入口 |

除 `device.py` 外都不碰硬件，可以直接用录下来的帧做单元测试。

## 已知限制

- **设备名不可写**。`0x7127` 只有读方向，固件没有接收名字的处理器。
- **切 UAC 会重枚举**。改成 `uac1` 后 USB 描述符重建，后续写返回 `-1`。切完等约 3 秒、重新打开。
- **10 个动作只能走 `raw`**：`factory-reset`、`disable-controller`、`call-c1/c2`、`execute-remote-a/b`、
  `heartbeat`、`save-c1/c2`、`clear-pairing`。它们没有回读状态。
- **切输出端口会连带改 EQ 和音量**。两者都按端口分别记忆，切回原端口即恢复。
- **`0x1206` 只读**。切预设用 `0x110E`。
- **`raw` 会拒绝两个命令**：`0x710B`（恢复出厂）与 `0x712E`（固件更新模式）。后者会让设备直接离开
  USB，只有重新刷写固件才能恢复。确需发送时加 `--force`。
- **云端配置的名字与滤波不可读**。Topping Home 在放云端配置时，设备只通过 `0x7137` 上报一个
  `source` 字节和两个哈希；`0x1206` 仍然指设备存储的本地槽位。`configs.playing` 表示实际在放的
  音源（`local`/`cloud`/`audition`/`unknown`），`configs.stored_index`/`stored_name` 表示存储的
  槽位，两者不会混。不是本地槽时 `playing.slot_index` 为 null。

## 测试

```bash
python -m unittest discover -s tests -t tests -v
```

109 个用例，只测纯函数（帧编解码、字段编码、PEQ 块编解码、帧序列解释、云端/本地音源判定、多帧收集状态机、写确认帧等待），不需要设备。

## 许可证

MIT，见 [LICENSE](LICENSE)。
