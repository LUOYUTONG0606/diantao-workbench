# diantao — 点淘 / 淘系 Android 自动化任务框架

## 推荐入口：点淘自动化大管家

双击 **`启动大管家.cmd`**，在同一看板管理全项目业务任务、原有一键流程以及赚能量的 OCR / WorkBuddy 版本。
手机二维码配对、任务搜索、启动/停止和执行日志已集成；首页的一键流程串行执行，避免多个任务争抢手机。
原 `启动工作台.cmd` 进入同一个看板。下载完整仓库后，先按
[工作台使用说明](workbench/README.md) 配置本机 OCR 服务和 ADB。
首次启动准备依赖，后续直接复用环境；墨绿、黑色和白色配色可独立切换并保存。

## v0.3.0 更新

- **`启动大管家.cmd`** 现在默认打开 9070 新看板，并自动启动真实的 9060 任务服务。
- 若 9060 已有兼容服务则直接复用；关闭看板时只清理本次启动器创建的后端，不终止外部服务。
- **`启动工作台.cmd`** 兼容转发到默认入口；**`启动旧版大管家.cmd`** 保留原 9060 独立窗口入口。
- 更新区域是明确标注的 **simulation**，目标 `v0.3.1-demo`；不会下载、安装、签名验证或承诺回退。
- 根 `VERSION.json`、运行时版本 API 与发布打包器统一使用 v0.3.0。
## 下载发布版与保留本机配置

当前版本：**v0.3.0（2026-10-29）**。

- **[夸克网盘下载 v0.3.0](https://pan.quark.cn/s/8b0cd4aa595a)**：永久有效，无需提取码。
- GitHub 备用下载：本仓库 **Releases** 中的 `diantao-workbench-v0.3.0.zip`。

从上述任一入口下载 `diantao-workbench-v0.3.0.zip`，解压后先双击
**`配置本机.cmd`**，再双击 **`启动大管家.cmd`**。首次启动会在缺少 `uv` 时安装它，
仍需自行安装 ADB 并准备自己的 OCR；这是源码运行包，不是免安装 EXE。

私有配置统一保存在 **`%LOCALAPPDATA%\DianTao\config.env`**，不放进发布包。
“配置本机”首次运行会复制安装目录已有的 `.env`；没有旧配置时使用 `.env.example`，
已有外部配置绝不覆盖。旧 `.env` 保留不动，请勿随文件夹转发。
配置修改后关闭再打开工作台。升级时先停止任务、关闭窗口，将新版解压到新目录即可，
同一 Windows 用户的外部配置会继续使用；新目录首次启动会重新准备依赖。

读取优先级：**进程环境变量 > 外部配置 > 旧项目 `.env`**。
如需多套配置，可设置 `DIANTAO_CONFIG_FILE` 指向自己的配置文件；
显式指定时只读取该文件，不再回退到项目 `.env`，文件不存在会报错。
这些文件是本机明文设置，请只放在自己的用户目录中，不要上传或分享。

维护者以后发布：

```powershell
# 在已脱敏的公开发布分支操作；不要推送含私有配置的开发历史
# 先完成相关测试，提交改动，确认工作区干净
python tools/build_release.py v0.3.0
# 如开发配置存放在其他位置，可追加 --private-config <配置文件路径>
gh release create v0.3.0 dist/diantao-workbench-v0.3.0.zip --target <已推送的公开提交号> --title "DianTao_Automation_Butler_v0.3.0" --notes-file <发布说明文件>
```

打包器从当前 **Git 提交**生成 ZIP，拒绝未提交修改，排除本机 `.env`、虚拟环境、
运行记录、截图和开发记忆，并检查常见凭证、私有网络 URL 及本机配置值。
只分发生成的 ZIP，不要直接压缩整个开发文件夹。自动检查用于阻止常见误传；
新增服务配置仍须使用环境变量，不能硬编码真实地址或密钥。

---

基于 **Android UIAutomator2 + 多模态视觉/OCR 双通道** 的淘系（点淘、淘宝、天猫、支付宝等）日常任务自动化框架。
核心思路：**原生控件优先、视觉/OCR 兜底、比例坐标保底**，在任务执行过程中只判断「任务是否完成」，异常场景保留现场。

> 本仓库为当前「淘金币双11赚能量」等任务的可运行版本快照。

## 给使用者及接手本项目的 AI：先准备自己的 OCR

**本项目不要求使用作者提供的 OCR，也不要求使用腾讯云。** 使用者可以在自己的电脑上安装 OCR，也可以使用自己的局域网、服务器或其他服务商提供的 OCR。作者的服务地址、账号及密钥不是安装前提，不应复制到他人的配置中。

当你是帮助他人安装、部署或运行本项目的 AI 时，请按下面的顺序操作：

1. 先询问或检查使用者已有的 OCR；已有服务可复用，不要重复安装。
2. 没有服务时，优先在使用者电脑上安装与其系统、Python 和硬件兼容的 OCR。下面的 PaddleOCR CPU 服务是一个可选示例；可以换成其他 OCR 引擎。
3. 运行 `配置本机.cmd`，将使用者自己的完整接口地址写入外部 `config.env` 的 `DIANTAO_OCR_SERVER_URL`，不要改成作者的服务，也不要提交真实配置。
4. 先检查服务健康状态，再用包含中文和按钮的样例截图验证文字及原图坐标，最后才连接手机执行任务。失败时说明安装、服务或接口错误，不能把失败当成“没有文字”而继续点击。

### 可选方案：在本机安装 PaddleOCR 服务

以下为 Windows PowerShell 的 CPU 部署示例。由接手的 AI 按使用者的系统和 PaddlePaddle 安装要求选择兼容版本；不需要购买云服务器、GPU 或 CDN。首次安装及模型下载需要联网，并会占用本机磁盘和运行资源。

```powershell
# 在本项目根目录创建独立 OCR 环境
py -3.10 -m venv .venv-ocr
.\.venv-ocr\Scripts\python.exe -m pip install --upgrade pip
.\.venv-ocr\Scripts\python.exe -m pip install "paddlepaddle>=3,<4" "paddleocr>=3,<4" fastapi uvicorn python-multipart

# 在一个单独的终端保持 OCR 服务运行；首次启动会下载模型
.\.venv-ocr\Scripts\python.exe -m uvicorn tools.ocr_compat_server:app --host 127.0.0.1 --port 8888
```

服务使用 PaddleOCR 3.x 的 `predict()` 接口。安装依据见 [PaddleOCR 官方快速开始](https://www.paddleocr.ai/main/quick_start.html)。若 Windows 的模型缓存路径包含中文而导致模型加载失败，请由接手的 AI 将 `PADDLE_PDX_CACHE_HOME` 设置为本机可写的英文路径后重试。

在另一个终端检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8888/health
# 确认 status 为 ready，再使用真实样例截图测试 /predict_ocr
.\配置本机.cmd
```

本机外部 `config.env` 示例：

```dotenv
DIANTAO_OCR_SERVER_URL=http://127.0.0.1:8888/predict_ocr
```

使用自己的远程 OCR 时，将其替换为使用者自己的服务地址；`127.0.0.1` 表示运行脚本的电脑。能量任务及共享任务库均读取这个配置，不会自动连接作者的 OCR。能量任务缺少配置时会在连接手机前停止；共享任务库会提示配置缺失并返回空识别结果。

### 使用其他 OCR 引擎或自己的服务

任务脚本通过统一 HTTP 契约接入 OCR，服务端可以使用 PaddleOCR 或其他引擎。请求为 `POST`，文件表单字段为 `image`（JPEG）；服务返回：

```json
{
  "code": 200,
  "data": [
    {"text": "去完成", "score": 0.98, "center": {"x": 1100, "y": 1490}}
  ]
}
```

`center.x/y` 必须是上传原图的像素坐标，`score` 为 0 至 1 的置信度；图片缩放后的坐标需要由服务端换算回原图。引擎的原始输出不符合此契约时，由接手的 AI 增加适配层，保留任务脚本的输入输出契约。服务访问权限、凭证和费用由使用者自行配置。

---

## 一、技术栈

| 层 | 技术 | 用途 |
| --- | --- | --- |
| 设备控制 | **Android UIAutomator2 (u2)** + ADB | 点击、滑动、返回、启动 App、`dump_hierarchy` 控件树、`watch_context` 弹窗监控 |
| 设备连接 | Wi-Fi ADB（`connect_with_retry`） | 断线重连，兼容单机/多机 `--serial` |
| 视觉识别 A（主） | **CodeBuddy 多模态大模型**（`codebuddy_vision.py`） | 语义定位文字/按钮/图标，直接返回物理坐标 `(cx, cy)`，可识别无字图标（关闭 X、返回箭头、红包等） |
| 视觉识别 B（备） | **使用者配置的 OCR**（本机或远程 HTTP 服务） | 全屏 OCR 文本块，返回 `{"text","cx","cy","conf"}` |
| 视觉识别 C（兜底） | 屏幕比例坐标 / 固定坐标 | 已知稳定入口的快速点击 |
| 图像处理 | OpenCV (`cv2`) + Pillow (`PIL`) | 截图裁剪、缩放、JPEG 压缩（降低 Token / 带宽） |
| 运行时 | Python 3.10、`uv`（`uv run --no-project`） | 独立环境运行，无需预装依赖 |
| OCR 兼容服务 | FastAPI + Pydantic + PaddleOCR | 把新版 PaddleOCR 结果转换为旧版 `predict_ocr` 契约 |
| 测试 | pytest / unittest | 契约测试、路由测试、计时守卫测试 |

**定位优先级**：`uiautomator2 原生控件 > CodeBuddy 多模态视觉 > 云端 PaddleOCR > 屏幕比例/固定坐标`

---

## 二、URL 与外部服务

| 名称 | 地址 / 标识 | 说明 |
| --- | --- | --- |
| OCR 服务地址 | 环境变量 `DIANTAO_OCR_SERVER_URL` | 使用者自己的完整 OCR 接口地址（含 `/predict_ocr` 路径），支持本机、局域网和远程服务 |
| OCR 接口 | `POST {DIANTAO_OCR_SERVER_URL}` | 表单字段 `image`（JPEG），返回 `{"code":200,"data":[{"text","score","center":{"x","y"}}]}` |
| OCR 兼容服务（本地/自建） | `tools/ocr_compat_server.py`，`uvicorn ... --port 8888` | 提供 `POST /predict_ocr`、`GET /health` |
| RAG 知识库 | `http://127.0.0.1:9621`（LightRAG，本地） | 开发规范检索，`rag_client.py` / `lightrag_helper.py` |
| 设备 | Wi-Fi ADB，如 `192.168.x.x:<port>` | 真机；脚本内通过 `--serial` 或默认单机选择 |
| 应用包名 | `com.taobao.live`（点淘）、`com.taobao.taobao`（淘宝）、`com.tmall.wireless`（天猫）、`com.eg.android.AlipayGphone`（支付宝） | — |

> 配置示例（PowerShell）：
> ```powershell
> $env:DIANTAO_OCR_SERVER_URL = "http://<your-ocr-host>:8888/predict_ocr"
> ```

---

## 三、OCR / 视觉识别管线

**1. 采集**：`device.screenshot()`（必要时 `adb exec-out screencap -p` 绕过 u2 覆盖层关闭问题）。

**2. 压缩**：截图等比缩放（全屏建议宽 632~720px），存 JPEG（质量 65~85），降低带宽与 Token。

**3. 识别（三级回退）**：
- **CodeBuddy 多模态**：`vision_engine.find_target(img, "去完成")` → `(cx, cy)`；`get_text_blocks()` → 文本块；`classify_screen()` → 页面语义分类；`analyze_guess_price()` → 猜价格决策。
- **云端 PaddleOCR**：`_cloud_ocr(img)` → `[{"text","cx","cy","conf"}]`（置信度 < 0.4 过滤）。
- **比例坐标**：`(0.90*sw, 0.42*sh)` 这类稳定入口。

**4. 坐标换算**：视觉模型常输出相对百分比或压缩图坐标，统一换算回**设备原始分辨率**（1264×2780）后再点击。

**5. 契约兼容**：`utils.get_ocr_text_blocks()` / `find_text_by_paddleocr()` / `paddle_ocr()` 保持旧入参与返回格式，旧脚本零改动即可切换后端。

**WebView 说明**：点淘部分活动页为 Canvas/Themis WebView，`dump_hierarchy` 无文本甚至长时间等待，此时直接走 OCR（`scan()` 中按 Activity 判断，Themis 页面用空树跳过 U2）。

---

## 四、面向对象设计

### 1. `CodeBuddyVision`（`codebuddy_vision.py`）
封装多模态视觉引擎，内部通过子进程调用本地 CodeBuddy CLI（`node codebuddy.js -p ... --tools Read`），把「自然语言目标」转为「屏幕物理坐标」。

| 方法 | 作用 | 返回 |
| --- | --- | --- |
| `find_target(image, target_description, region=None)` | 语义定位任意元素（文字/按钮/图标）中心点 | `(cx, cy)` / `None` |
| `get_text_blocks(image, min_conf=0.5)` | 全量文字块（兼容 PaddleOCR 格式） | `[{"text","cx","cy","conf"}]` |
| `paddle_ocr(image)` | 全图文字拼接 | `str` |
| `classify_screen(image, candidates)` | 页面语义分类 | 分类名 |
| `analyze_guess_price(image)` | 猜价格：识别商品/高低提示/出价框，给出下次出价 | `dict` |
| `_prepare_image_file / _invoke_cli / _extract_json`（私有） | 压缩落盘、调用 CLI、提取 JSON | — |

全局单例 `vision_engine = CodeBuddyVision()`；模块级兼容函数 `codebuddy_ocr / find_text_by_codebuddy / paddle_ocr_cb`。

### 2. `SmartNavigator`（`smart_navigator.py`）
两层状态机导航器：**先异常过滤，再极速定位**。

| 方法 | 作用 |
| --- | --- |
| `capture_screen(prefix)` | 截图并压缩到宽 720 / 质量 65 |
| `query_vision_fast(img_path, prompt)` | 单轮调用视觉模型 |
| `check_and_handle_exceptions()` | 第一层：识别遮罩/弹窗/关闭 X 并自动消除 |
| `locate_target(target_name)` | 第二层：按百分比定位目标 `(cx, cy)` |
| `enter_target(target_name)` | 组合：异常处理 → 定位 → 点击进入；失败保留现场截图 |
| `_load_rules()` | 加载 `nav_rules.json` 素材/规则库 |

### 3. `diantao_1024_auto.py`（2048/1024 数字合成游戏 AI）
经典 OOP 分层，职责单一：

- `Config`：校准参数加载（`load_calibration`）。
- `ADBController`：`screenshot / swipe / tap / get_screen_size / get_device_info` 底层 ADB 操作。
- `BoardRecognizer`：`crop_board / crop_cell / _cloud_ocr / recognize_board / visualize`，把棋盘截图识别为 4×4 数字矩阵（OCR 坐标按缩放比例映射回原图）。
- `GameEngine`：纯函数式棋盘规则（`move_left/right/up/down`、`is_game_over`、`empty_cells`、`max_value`）。
- `GameAI`：`expectimax` 期望最大搜索（`evaluate / decide / decide_fast`）。
- `AutoPlayer`：`calibrate / run_single_game / run / _summary`，串联识别→决策→操作。

### 4. `tools/ocr_compat_contract.py` + `tools/ocr_compat_server.py`（OCR 兼容服务）
- `ocr_compat_contract.py`：纯转换层，`OcrCenter / OcrBlock`（`@dataclass(frozen=True, slots=True)`）、`normalize_paddle_result()` 把新版 PaddleOCR 的 `rec_texts / rec_scores / rec_boxes|dt_polys` 转为旧契约，并用 `_center_from_box()` 兼容「四点框 / 扁平分值」两种坐标格式。
- `ocr_compat_server.py`：FastAPI 应用，`create_app(engine)` 依赖注入 OCR 引擎，提供 `POST /predict_ocr`、`GET /health`；`Protocol`（`PaddleEngineLike / PaddleResultLike`）+ Pydantic 响应模型（`OcrResponse / OcrBlockResponse / CenterResponse`）保证类型契约；`OcrInputError / OcrInferenceError` 区分输入与推理错误。

### 5. 任务执行函数库（`u2_scanner.py` / `utils.py`）
以函数式为主，按「扫描 → 路由 → 执行 → 复核」拆分：
- `scan_page` / `_ocr_nodes` / `u2_find_task_buttons`：扫描任务列表。
- `execute_live_task` / `execute_video_task` / `execute_browse_task`：三类任务执行器（直播每 15s 上滑 `-10s`；视频按次数+最低停留；浏览匀速微滑）。
- `handle_guess_price_game`：猜价格二分/重试。
- `back_to_task` / `wait_completion` / `is_blacklisted`：返回、等待、黑名单。
- `setup_global_watcher`：Android 系统权限弹窗监控（页面弹窗交给 OCR，避免 U2 读 WebView）。

---

## 五、常用入口

| 文件 | 用途 |
| --- | --- |
| `启动大管家.cmd` | **点淘 · 自动化大管家**：全项目任务、淘金币双11能量 OCR/WorkBuddy 版、扫码连接、日志和主题；详见 [`workbench/README.md`](workbench/README.md) |
| `一键全部执行.py` | **点淘一键全流程**：连通性 → 下滑 → 日常任务 → 养优惠（独立入口，`--list` 查看顺序；全程不设超时，Ctrl+C 停止） |
| `run_taobao_energy.cmd` | **淘金币双11赚能量**入口：`run / enter / scan / auto / browse` |
| `淘金币任务.py` | **双十一赚淘金币**：首页 → 领淘金币 → 去赚淘金币（兼容“去赚金币”）；OCR 定位、逐项复核，支持 `run / enter / scan`，详见 [`docs/taobao-coins.md`](docs/taobao-coins.md) |
| `tools/taobao_energy.py` | 赚能量核心：U2+OCR 扫描、三梯队（淘宝→支付宝→其他应用）、逐项浏览并复核 |
| `一键下滑_新.py` / `一键下滑_AI.py` | 打工、睡觉、签到、购物、摇一摇、走路 六轮下滑 |
| `签到赢元宝.py` / `签到赢元宝_AI.py` | 每日签到与里程碑 |
| `打工得体力.py` / `睡觉赚元宝.py` / `走路赚元宝.py` | 对应任务 |
| `点淘鸭钻石任务.py` / `点淘鸭摇一摇.py` / `赚钱卡.py` / `点淘购物赚钱.py` | 对应活动 |
| `全部执行.py` / `任务选择器.py` | 按 `task_config.json` 批量执行 / GUI 勾选 |
| `verify_all_tasks.py` | 端到端验证套件 |

运行示例：
```powershell
# 打开与 GitHub 发布版一致的大管家桌面窗口
.\启动大管家.cmd

# 点淘一键全流程（连通性 → 下滑 → 日常任务 → 养优惠）
py -3.10 一键全部执行.py

# 淘金币双11赚能量（需先设置 DIANTAO_OCR_SERVER_URL）
.\run_taobao_energy.cmd run --repeat 30

# 通用任务
py -3.10 打工得体力.py
```

在自动化大管家中注册为「Python脚本」入口（大管家重启后载入）：

```powershell
py -3.10 deploy/register_all_in_one.py "<automation-butler目录>"
```

---

## 六、环境与依赖

- Python 3.10；`uiautomator2`、`opencv-python`、`Pillow`、`requests`、`python-dotenv`（读取本机 OCR 配置）、`fastapi`/`uvicorn`（兼容服务）、`pytest`。
- 真机需开启 USB/Wi-Fi 调试；脚本使用 Wi-Fi ADB 地址连接。
- OCR 通过 `DIANTAO_OCR_SERVER_URL` 注入；能量任务未设置时在连接手机前停止，共享任务库未设置时提示并跳过识别。不会自动安装 OCR，也不会回退到作者的服务。

## 七、安全与边界

- **黑名单**：`下单 / 购买 / 付款 / 充值 / 元宝商城 / 邀请 / 分享 / 蚂蚁庄园 / 饿了么 / 消消乐` 等一律跳过，不产生真实资金支出与社交骚扰。
- **跨应用**：只点系统「仅本次允许 / 打开」，不改长期权限；未安装应用只查看入口后返回，不下载、不安装。
- **风控**：检测到登录/账号异常立即停止；操作间隔拟人化。
- **现场保留**：未知页面/授权/登录障碍保留截图，交由人工检查。
- **凭据**：仓库不含任何 API Key / Token / 密码；OCR 服务地址通过环境变量注入。
