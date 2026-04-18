# nanobanana

Google Gemini 专属的轻量 ACPX Image Agent，支持多会话并发图片生成与多轮编辑。

## 特性

- **多会话并发** — 同时维护多个独立 Gemini Chat 实例，会话间完全隔离
- **同名自动替换** — `session/new` 同名会话时静默替换旧会话，无需手动删除
- **多轮图片修改** — 完全复用 Gemini 原生上下文，后续修改无需重新传图
- **多模态输入** — 支持图片参考文件作为输入（最多 14 张）
- **流式输出** — 文本与图片实时流式返回
- **无状态部署** — 会话仅在进程内存中存在，无数据库依赖

## 安装

```bash
pip install -e .
```

依赖：`google-genai >= 1.0.0`、`Pillow >= 10.0.0`、Python >= 3.11

## 配置

```bash
# 必须
export GOOGLE_API_KEY="AIzaSy..."

# 可选，默认为 gemini-3.1-flash-image-preview
export NANOBANANA_DEFAULT_MODEL="gemini-3.1-flash-image-preview"

# 可选，开启调试日志（输出到 stderr 和 /tmp/nanobanana.log）
export NANOBANANA_DEBUG=1
```

## 接入 acpx

将 `acpx.config.json` 放置于项目根目录，acpx 会自动识别：

```json
{
  "agents": {
    "nanobanana": {
      "command": "nanobanana"
    }
  }
}
```

或通过 `--agent` 标志直接调用：

```bash
acpx --agent nanobanana 'session/new --name project1'
```

## 使用示例

```bash
# 新建会话
acpx nanobanana session new --name projectA

# 文生图
acpx nanobanana -s projectA "生成一张赛博朋克风格的猫"

# 多轮修改（无需重传图片，Gemini 自动维护上下文）
acpx nanobanana -s projectA "把猫的眼睛改成蓝色"
acpx nanobanana -s projectA "添加霓虹灯背景"

# 图生图（传入参考图片）
acpx nanobanana -s projectA --file reference.jpg "参考这个风格重新生成"

# 并发使用多个独立会话
acpx nanobanana session new --name projectB
acpx nanobanana -s projectB "生成一张水墨风格的山水画"

# 同名新建 → 自动销毁旧会话，上下文清空
acpx nanobanana session new --name projectA

# 查看所有运行中的会话
acpx nanobanana session list

# 删除会话
acpx nanobanana session delete projectA
```

## ACP 协议

nanobanana 实现 [Agent Client Protocol](https://github.com/agentclientprotocol/agent-client-protocol)，通过 stdin/stdout 以 JSON-RPC 2.0 + Content-Length 帧通信。

**支持的方法：**

| 方法 | 说明 |
|------|------|
| `initialize` | 能力协商 |
| `session/new` | 创建或替换命名会话 |
| `session/list` | 列出所有活跃会话 |
| `session/delete` | 删除指定会话 |
| `session/prompt` | 发送提示词，流式返回内容块 |
| `shutdown` / `exit` | 优雅退出 |

**流式响应通知（`session/update`）内容块格式：**

```json
{ "type": "text",  "text": "这是一段描述文字" }
{ "type": "image", "data": "<base64>", "mime_type": "image/png" }
```

## 错误处理

| 错误 | 返回信息 |
|------|----------|
| 会话不存在 | `会话 [name] 不存在，请先用 session/new 创建` |
| API Key 无效 | `Google API Key 无效，请检查配置` |
| 配额不足 | `API 配额不足，请稍后再试` |
| 内容被拦截 | `生成内容不符合安全政策，请调整提示词` |
| 环境变量缺失 | 启动时立即退出并通过 `agent/fatalError` 通知 acpx |

## 项目结构

```
nanobanana-cli/
├── pyproject.toml
├── acpx.config.json
└── src/nanobanana/
    ├── __main__.py   # 入口
    ├── server.py     # ACP JSON-RPC 服务器
    ├── session.py    # 运行时会话管理
    └── gemini.py     # Gemini SDK 封装
```
