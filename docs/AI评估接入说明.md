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

在仓库根目录 `config.json` 增加 `ai` 段（默认关闭）：

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

`base_url` 需指向提供 OpenAI 兼容 `/chat/completions` 的服务，例如：

| 服务 | base_url 示例 |
| --- | --- |
| Ollama | `http://localhost:11434/v1` |
| vLLM | `http://localhost:8000/v1` |
| LM Studio | `http://localhost:1234/v1` |
| 云端 OpenAI 兼容网关 | 由服务商提供 |

API Key 通过**环境变量**注入，不写入仓库文件：

```powershell
$env:UAV_AI_API_KEY = "sk-..."
```

也支持环境变量整体覆盖（无需改仓库文件）：

```powershell
$env:UAV_AI_ENABLED   = "true"
$env:UAV_AI_BASE_URL  = "http://localhost:11434/v1"
$env:UAV_AI_MODEL     = "qwen2.5:7b"
```

加载优先级：**环境变量 > config.json 的 `ai` 段 > config.py 默认值**。

---

## 3. 接口契约

### 3.1 可用性探测

```
GET /api/assess/report/status
```

```json
{ "llm_enabled": false, "llm_ready": false, "model": null, "base_url_configured": false }
```

前端可据此决定是否展示「LLM 生成」标签；该接口不返回任何密钥信息。

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
| `error` | LLM 失败，即将降级；`message` 为 причины说明 |
| `done` | 报告结束 |

`payload` 直接透传功能一 / 功能二的响应体，后端**只读取其中字段**、不额外取数。

---

## 4. 防幻觉设计

1. **输入即事实边界**：`build_feasibility_context()` / `build_time_window_context()` 只从结构化
   结果中抽取白名单字段（结论、距离、风险、分段、阻塞区、备用时段等），其余字段不进入 prompt。
2. **系统提示词约束**（`config.AI_SYSTEM_PROMPT`）：明确要求「只允许引用给定 JSON 中的数值与地名」
   「禁止编造任何气象事实」「不确定时使用『数据显示』措辞」。
3. **报告六段结构**固定为：结论概述 / 航线地理叙事 / 气象解读 / 时间建议 / 操作建议 / 数据口径声明。
4. **无法验证即降级**：LLM 不可用时规则版报告仍严格基于同一份结构化数据渲染。

---

## 5. 报告内容示例（规则模板版节选）

```
## 结论概述
本次航线评估结论为「可飞」。全程风险等级为 低风险，航线总距离 321.99 km，预计飞行 5.37 h。

## 航线地理叙事
航线由 拉萨市区附近 飞往 林芝以北约21km，直线距离 312.60 km。
按途经城镇切分，各段风险依次为：拉萨市区附近→墨竹工卡（低风险，58.0 km）；...

## 气象解读
沿航线采样 70 个格点，最大 TKE 0.3810、最大 EDR 0.0012，中度以上风险格点占比 0.0%。

## 时间建议
绕行额外耗时 0.16 h，当前优先级允许上限 2.61 h，在该上限之内。

## 操作建议
当前条件下可执行任务，建议按计划起飞并保持对湍流高发区段的关注。

## 数据口径声明
本报告基于 500hPa 等压面气象数据，空间分辨率约 5km、时间分辨率逐小时；TKE / EDR 为基于三维风场的代理估算值，不构成真实气象观测。
```

---

## 6. 测试

```powershell
cd backend
& ".python\portable\python.exe" -m unittest discover -s tests -p "test_assessor.py"
```

覆盖点：上下文字段抽取与空 payload 容错、规则报告六段结构与不臆造数值、
SSE 事件序列（meta → delta → done）、LLM 不可达时的降级路径、SSE 解析器的正常与畸形输入、
prompt 防幻觉约束。

---

## 7. 已知限制

- 报告为**一次生成、不保留会话历史**；如需多轮追问，需要另开会话状态设计。
- LLM 版未做 token 计费与速率限制，公网网关部署时建议自行加配额。
- 规则模板版不做语气润色，如需更自然的中文表达请启用 LLM 路线。
