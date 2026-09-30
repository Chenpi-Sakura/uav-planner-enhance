# AI 辅助评估接入说明（功能三）

> 版本：v1.0（2026-09-19）
> 上游文档：`docs/决策平台实现计划.md` §6
> 相关代码：`backend/assessor.py`、`POST /api/assess/report`、`frontend/src/components/ReportCard.vue`

---

## 1. 两条落地路线

按计划 §6.3 的推荐组合实现：

| 路线 | 说明 | 默认状态 |
| --- | --- | --- |
| A. 规则模板版 | 由结构化评估结果直接渲染中文报告，完全离线、确定性、可测试 | **默认启用（兜底）** |
| B. LLM 版 | OpenAI 兼容接口，**支持流式输出**；未配置或调用失败时自动降级到 A | 默认关闭，按需开启 |

**降级契约**：任何 LLM 异常（未配置、超时、HTTP 错误、无增量返回）都会自动回退到规则模板版，
并向前端推送一条 `error` 事件说明原因。因此**演示环境不配置任何 Key 也能完整跑通报告功能**。

---

## 2. 配置方式

### 2.1 推荐：`.env`（本地开发）

仓库根目录提供模板 `.env.example`（`.env` 本身已被 `.gitignore` 忽略，不会提交）。
首次使用先复制模板：

```powershell
cd G:\uav-planner-enhance
Copy-Item .env.example .env
```

然后编辑 `.env`，填三个必填项即可：

```dotenv
UAV_AI_ENABLED=true
UAV_AI_BASE_URL=https://your-gateway.example.com/v1
UAV_AI_MODEL=gpt-4o-mini
UAV_AI_API_KEY=sk-xxxxxxxx
# 可选，留空用默认值
UAV_AI_TIMEOUT_S=60
UAV_AI_MAX_TOKENS=900
UAV_AI_THINKING=disabled        # 推理型模型的思考开关，见 §2.5
UAV_AI_STRIP_THINKING=true      # 响应侧兜底过滤，一般不用改
```

改完**重启后端**生效，再用 `GET /api/assess/report/status` 确认：

```powershell
Invoke-RestMethod http://127.0.0.1:9090/api/assess/report/status
# { "llm_enabled": true, "llm_ready": true, "model": "gpt-4o-mini", ... }
```

**`UAV_AI_BASE_URL` 不要带 `/chat/completions`**，后端会自动拼接；结尾多余的 `/` 会被去掉。

`base_url` 需指向提供 OpenAI 兼容 `/chat/completions` 的服务，例如：

| 服务 | base_url 示例 | 需要 Key |
| --- | --- | --- |
| 云端 OpenAI 兼容网关 | 由服务商提供 | 是 |
| OpenAI | `https://api.openai.com/v1` | 是 |
| Ollama（本地） | `http://localhost:11434/v1` | 否，留空即可 |
| vLLM | `http://localhost:8000/v1` | 视部署而定 |
| LM Studio | `http://localhost:1234/v1` | 否，留空即可 |

### 2.2 备选：真实环境变量

不建 `.env` 也可以，直接用系统环境变量（CI / 容器推荐，且**优先级高于 `.env`**）：

```powershell
$env:UAV_AI_ENABLED  = "true"
$env:UAV_AI_BASE_URL = "https://your-gateway.example.com/v1"
$env:UAV_AI_MODEL    = "gpt-4o-mini"
$env:UAV_AI_API_KEY  = "sk-..."
```

### 2.3 备选：`config.json` 的 `ai` 段

`config.json` 是**被 git 跟踪**的文件，只适合放非敏感项（开关 / URL / 模型）。
**不要把 API Key 写进这里**；Key 请始终走 `.env` 或环境变量。

```json
{
  "backend": { "host": "0.0.0.0", "port": 9090 },
  "ai": {
    "enabled": true,
    "base_url": "http://localhost:11434/v1",
    "model": "qwen2.5:7b",
    "api_key_env": "UAV_AI_API_KEY",
    "timeout_s": 60,
    "max_tokens": 900,
    "temperature": 0.3
  }
}
```

其中 `api_key_env` 指定「去哪个环境变量名里取 Key」，默认 `UAV_AI_API_KEY`，
便于同一份代码在不同部署环境复用不同的变量命名。

### 2.4 加载优先级

**真实环境变量 > `.env` > `config.json` 的 `ai` 段 > `config.py` 默认值。**

即：已存在的系统环境变量不会被 `.env` 覆盖（便于临时用 `$env:...` 压过配置做调试）；
`.env` 文件不存在时程序照常启动，并按「未配置 → 规则模板版」自动降级。


### 2.5 推理型模型的「思考输出」控制

MiniMax-M3、DeepSeek-R1、QwQ 这类推理模型会先生成一段思考过程再写正文。
如果原样透传，前端报告开头就会多出一大段「让我先分析一下数据……」式的自言自语，
既难看又容易被误读成结论。为此做了**两道防线**：

| 防线 | 配置项 | 作用 | 默认 |
| --- | --- | --- | --- |
| 请求侧 | `UAV_AI_THINKING` | 在请求体里发送 `thinking={"type": "disabled"}`，直接让模型不要输出思考 | `disabled` |
| 响应侧 | `UAV_AI_STRIP_THINKING` | 忽略 `delta.reasoning*` 等推理字段，并从正文中剥离内联思考标签 | `true` |

取值说明：

- `UAV_AI_THINKING=disabled`：推荐值，MiniMax-M3 / 兼容该参数的网关实测有效。
- `UAV_AI_THINKING=adaptive`：由模型自行决定是否思考（正文仍可能混入旁白，仅用于对比测试）。
- `UAV_AI_THINKING=`（留空）：**不发送该字段**，供 Ollama / vLLM / LM Studio 等不认识它的网关使用。
  此时若网关因未知参数返回 4xx，后端会自动去掉该字段重试一次，而不是整份报告退化成规则版。

响应侧过滤只按**已知标签名**成对剥离（`think` / `thinking` / `thought` / `reasoning` /
`REASONING_SCRATCHPAD`），并且能处理标签被 SSE 分块切断的情况；
未闭合的思考块残稿整段丢弃，其余正文逐字保留（含中文标点与单独的 `<` 号）。
提示词层面也同步要求「直接给出报告正文，不要输出思考过程、草稿、自我说明」。

---

## 3. 接口契约

### 3.1 可用性探测

```
GET /api/assess/report/status
```

```json
{ "llm_enabled": false, "llm_ready": false, "model": null, "base_url_configured": false }
```

前端可据此决定是否展示生成标签（内容由模型生成时显示生成时刻 `HH:MM`，降级为规则模板时显示「规则兜底」）；该接口不返回任何密钥信息。

### 3.2 流式生成报告的正文

```
POST /api/assess/report
Content-Type: application/json

{ "task": "feasibility" | "time_window", "payload": { ...结构化评估结果... } }
```

响应为 `text/event-stream`，逐段推送：

```
data: {"type":"meta","source":"rule"}
data: {"type":"delta","text":"## 结论概述\n本次航线评估结论为「可飞」..."}
data: {"type":"done"}
```

| 事件 | 含义 |
| --- | --- |
| `meta` | 报告来源：`llm`（模型生成）/ `rule`（规则模板） |
| `delta` | 正文增量，前端应**追加**渲染（实现流式打字机效果） |
| `error` | LLM 失败，即将降级；`message` 为原因说明 |
| `done` | 报告结束 |

`payload` 直接透传功能一 / 功能二的响应体，后端**只读取其中字段**、不额外取数。

---

## 4. 防幻觉设计

1. **输入即事实边界**：`build_feasibility_context()` / `build_time_window_context()` 只从结构化
   结果中抽取白名单字段（结论、距离、风险、分段、阻塞区、备用时段等），其余字段不进入 prompt。
2. **系统提示词约束**（`config.AI_SYSTEM_PROMPT`）：明确要求「只允许引用给定 JSON 中的数值与地名」
   「禁止编造任何气象事实」「不确定时使用『数据显示』措辞」「正文里不得出现 JSON 的字段名或键名」。
3. **报告五段结构**固定为：结论概述 / 航线地理叙事 / 气象解读 / 时间建议 / 操作建议。
   不设「数据口径声明」段——分辨率、时区换算这类是给开发者看的元信息，占着报告一节却挤掉飞行结论。
4. **时间口径只声明一次**：上下文里的 `takeoff` / `landing` / `slot.time` 等字段已由后端换算成北京时间，
   `build_*_context()` 因此会剔除内部的 `data_label_is_utc` 标志，并附一句 `time_base` 声明
   （系统提示词同步禁止再做时区换算）。否则模型会拿 `tz_offset_hours` 自行 +8，
   产出与面板矛盾的起飞时间。
5. **给结论，不给数据**：每节先下判断，再补一两个支撑数值。逐项罗列原始字段的写法已明确禁止，
   用户要的是「能不能飞、有什么风险、该怎么飞」，不是一份数据表。
6. **无法验证即降级**：LLM 不可用时规则版报告仍严格基于同一份结构化数据渲染。

### 4.1 公式渲染

正文里的数学式一律写成行内 LaTeX（`$...$`），由前端 KaTeX 排版。
分数指数（`m^(2/3)/s`）在 Unicode 里没有对应上标码位，只能拼 ASCII，
印在正文里像乱码，因此 `assessor.EDR_UNIT` / `TURB_NOTE` / 字段表里的 EDR 口径
都直接写成 `$\mathrm{m}^{2/3}/\mathrm{s}$`，提示词也要求模型**连着 `$` 一起照抄**、
不要「翻译」回 ASCII 写法。

渲染管线在 `frontend/src/utils/markdown.ts`，有两处顺序上的讲究，改动时务必保留：

1. **公式必须在 marked 之前摘出来**。LaTeX 里的 `_`、`^` 是 Markdown 的强调/下标标记，
   `$x_i$` 经 marked 会变成斜体的 `x`，上下标直接消失 —— 到 marked 之后再处理就晚了。
   摘出的片段先换占位符，等 `DOMPurify.sanitize` 跑完再把 KaTeX 的 HTML 填回去。
2. **公式 HTML 必须在净化之后填**。KaTeX 输出的 `<math>` / `<annotation>` 会被
   DOMPurify 按 HTML profile 剥掉，先填就只剩空壳；后填则绕过了净化，
   所以 KaTeX 必须关掉一切危险能力（`trust: false` + `strict: 'ignore'` + `output: 'html'`）。

代价是包体增加约 +78 kB（gzip）JS 与 +8 kB（gzip）CSS。

---

## 5. 报告内容示例（规则模板版节选）

```
## 结论概述
本次航线评估结论为「可飞」。全程风险等级为 低风险，航线总距离 321.99 km，预计飞行 5.37 h。

## 航线地理叙事
航线由 拉萨市区附近 飞往 林芝以北约21km，直线距离 312.60 km。
按途经城镇切分，各段风险依次为：拉萨市区附近→墨竹工卡（低风险，58.0 km）；...

## 气象解读
沿线湍流整体可控，没有成片的高危区。
全程最大 TKE 0.3810、最大 EDR 0.0012，中度以上风险格点占比 0.0%。

## 时间建议
绕行代价在当前优先级允许的上限之内，额外耗时 0.16 h，上限 2.61 h。

## 操作建议
当前条件下可执行任务，建议按计划起飞并保持对湍流高发区段的关注。
```

---

## 6. 测试

### 6.1 离线单测（不需要 Key）

```powershell
cd backend
& ".python\portable\python.exe" -m unittest discover -s tests -p "test_assessor.py"
```

覆盖点：上下文字段抽取与空 payload 容错、规则报告六段结构与不臆造数值、
SSE 事件序列（meta → delta → done）、LLM 不可达时的降级路径、SSE 解析器的正常与畸形输入、
prompt 防幻觉约束、**思考输出控制**（请求参数、4xx 重试、reasoning 字段忽略、
内联标签跨块剥离）。

### 6.2 连通性与「思考残留」自检（需要真实 Key）

不启动后端也能直接打网关，一次调用同时回答「参数是否被接受」和「正文是否干净」：

```powershell
cd backend
& ".python\portable\python.exe" scripts\check_ai_report.py
```

输出示例（MiniMax-M3 实测）：

```
  thinking 参数被网关接受 : True
  reasoning 字段字符数    : 0
  正文内联思考标签命中    : 0
  原始正文字符数          : 1008
  兜底过滤后字符数        : 1008
```

退出码：`0` 正文干净 / `1` 未配置或调用失败 / `2` 检测到思考残留（已兜底过滤后展示）。

### 6.3 端到端（前端）

```powershell
cd G:\uav-planner-enhance
.\start.bat            # 后端 9090 + 前端 5173
```

1. 先确认配置已加载：`Invoke-RestMethod http://127.0.0.1:9090/api/assess/report/status`
   应返回 `llm_ready: true` 与所填模型名；
2. 打开「可行性评估」页 → 设置起终点 → 点「开始评估」→「生成 AI 报告」，
   报告卡片应显示生成时刻标签（形如 `23:04`），正文以 `结论概述` 小节开头，**前面没有任何分析旁白**；
3. 「时间窗口」页同样检查一次（该路径复用同一个流式接口），并核对报告里的起飞/降落时刻
   **与上方结果框完全一致**——若报告出现「UTC」或比面板晚 8 小时，说明时间口径声明未生效；
4. 小标题允许两种写法：`## 标题` 与整行 `**标题**`，行内 `**加粗**` 也会被渲染，
   因此正文里不应看到裸露的星号（卡片渲染器 `ReportCard.vue` 已做转义与替换）；
5. 想验证降级链路：把 `.env` 里 `UAV_AI_ENABLED` 改成 `false` 重启后端，
   报告应变成「规则模板」来源且内容完整。

> 手工起后端时请用 `-m uvicorn`，不要直接 `python main.py`：
> `backend/.python/portable` 是嵌入式发行版（带 `python312._pth`），不会把脚本所在目录加入
> `sys.path`，直接跑 `main.py` 会报 `ModuleNotFoundError: No module named 'schemas'`。
>
> ```powershell
> cd G:\uav-planner-enhance\backend
> & ".python\portable\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 9090
> ```
> 首次启动要预加载 NetCDF 与 SRTM，实测约 5 分钟才监听端口。

---

## 7. 已知限制

- 报告为**一次生成、不保留会话历史**；如需多轮追问，需要另开会话状态设计。
- LLM 版未做 token 计费与速率限制，公网网关部署时建议自行加配额。
- 规则模板版不做语气润色，如需更自然的中文表达请启用 LLM 路线。
- **正文长度受 `UAV_AI_MAX_TOKENS` 限制**：六段中文报告实测需要 1200 token 以上，
  默认 900 会让「时间窗口」报告在末段被截断（句子中途收尾）。遇到这种情况请调大该值，
  而不是认为模型出错。
