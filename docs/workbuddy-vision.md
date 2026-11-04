# 赚能量 WorkBuddy 视觉版

这个版本复用原任务脚本，仅用 WorkBuddy 视觉模型替代 OCR。U2 仍负责控制手机，并优先读取可用的界面文字；WebView 没有可用文字时才调用 WorkBuddy。模型只抄录文字与中心坐标，不判断任务优先级、能量、到账或下一步动作。原来的跳过规则、任务进度核对和运行时限继续由脚本处理。

默认启动方式继续使用原识别后端。WorkBuddy 版单独启动，无需修改默认配置。

## 本机准备

安装并登录 WorkBuddy，选择支持读取图片的模型。JavaScript CLI 需要 Node.js。程序复用观众自己电脑上的 CLI 与登录，不分发作者的登录信息、服务地址或模型配置。

在 `%LOCALAPPDATA%\taobao1111\workbuddy.json` 保存自己的配置，格式参考项目根目录的 `workbuddy.example.json`，将 `cli_path` 替换为本机 WorkBuddy 附带的 `resources/app.asar.unpacked/cli/dist/codebuddy.js` 文件完整路径。`model` 为本机可用的视觉模型 ID，`timeout_seconds` 为每次识别超时，范围 1–180 秒。

也可使用环境变量 `WORKBUDDY_CLI` 和 `WORKBUDDY_MODEL` 覆盖路径与模型。程序会检查常见的 WorkBuddy 安装位置；自定义安装位置应显式配置。配置文件留在用户数据目录，不提交 Git。

WorkBuddy 会将截图交给其配置的模型服务处理，因此该模式不属于离线本地 OCR。是否留存会话及截图内容取决于本机 WorkBuddy 和模型服务设置。适配器将图片编码后直接通过标准输入传给 CLI，在隔离的临时工作目录启动，不另存临时截图，也不把 CLI 原始会话、思考、账号元数据或截图上传到 GitHub。

## 启动

源码独立项目双击 `启动WorkBuddy版.cmd` 打开管家窗口，再手动启动任务；也可双击 `run_taobao_energy_workbuddy.cmd` 直接运行任务。进入任务进程后，日志会显示 `WorkBuddy视觉`。

命令行：

```powershell
uv run python tools/taobao_energy.py run --repeat 30 --vision workbuddy
uv run python tools/taobao_energy.py scan --vision workbuddy
```

原大管家项目提供同名 `run_taobao_energy_workbuddy.cmd`；这个入口复用已有的能量任务 Python 环境。识别过程失败时停止，不自动切换到云 OCR 或本地 OCR。

## 只输出 JSON

不连接手机、不执行任务，只识别一个图片文件：

```powershell
uv run python workbuddy_vision.py screenshot.png
```

成功时 stdout 只输出一条 UTF-8 JSON：

```json
{"width":600,"height":900,"blocks":[{"text":"做任务赚能量","cx":237,"cy":93},{"text":"好物沉浸看(7/10)","cx":175,"cy":272},{"text":"去完成","cx":483,"cy":272}]}
```

失败时返回非零退出码，错误只写 stderr，stdout 不伪造成功 JSON。`scan --vision workbuddy` 的 stdout 则保留任务扫描协议，包含 `app`、`source`、`blocks`。

CLI 的 `--output-format json` 是外层会话格式，不能保证模型内容已经符合业务 JSON。本机实测可能返回事件数组，最终结果在唯一的 `type=result` 项中，`result` 又可能是 JSON 字符串或完整 Markdown JSON 代码块。适配器只读取最终成功结果，忽略思考与工具输出，再校验字段、图像尺寸、文字类型和坐标范围。夹带解释文字、多个结果、错误结果或越界坐标均拒绝使用。

2026-10-26 手机实测时，CLI 曾返回无法解析的 JSON，或给文字块擅自增加 `blockNote` 字段，而同一张截图再次识别成功。适配器遇到外层或最终内容无法解析为 JSON、JSON 字段不符合协议时，最多重新识别同一张图一次；重试期间不操作手机。再次失败则停止。CLI 报错、权限拒绝、尺寸不符和坐标越界仍立即停止，不通过截取、拼接输出或丢弃多余字段放宽校验。

Windows 上 CLI 退出后，临时工作目录可能仍短暂被占用。临时目录清理采用尽力而为方式，避免清理时的文件占用错误丢弃已经校验通过的识别结果；这不影响 CLI 调用失败或识别结果无效时停止任务。

`--json-schema` 需要开放 CLI 的 `StructuredOutput` 工具。2026-10-26 已实测仅开放这个 JSON 提交工具即可返回有效结果，因此适配器使用 `--tools StructuredOutput --allowedTools StructuredOutput`，并提交限定字段、图像尺寸和坐标范围的 schema。最终结果仍须经过本地严格校验。图片通过 `--input-format stream-json` 作为图像内容直接传入；文件、Shell、手机操作工具、额外 MCP 配置和用户钩子均未开放。

长截图按宽度最多 1440 像素缩放，再切成高度最多 1440 像素、相邻重叠 96 像素的图片分别识别。常见 1264×2780 手机截图保留原宽，分两段读取，避免把任务小字压缩到难以辨认。模型返回各段实际输入图片的坐标，代码补回纵向偏移并映射到原截图，合并重叠区相同文字。所有分段均成功才返回观察结果，不使用失败前的局部坐标。

2026-10-26 实测：整屏缩至 655×1440 时，模型曾把“好物随心逛(2/5)”误读成搜索任务；同一区域保持原始尺寸裁剪后读对。直接传入整张原图也曾出现模型内部缩放后坐标不符，因此采用分段输入。结构化输出能限制字段和数值范围，不能证明文字和位置必然准确，实机核对仍须关注识别质量。

此改动提供源码入口；此前的成品 ZIP 尚未包含此模式，不能仅复制 CMD 到旧 EXE 目录使用。
