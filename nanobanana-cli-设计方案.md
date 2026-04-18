# nanobanana CLI 设计方案（极简最终版）

**版本：** v2.0-minimal  
**日期：** 2026-04-18  
**核心原则：** 最小可行产品，仅保留核心能力，无冗余功能

---

## 1. 核心定位
nanobanana 是 Google Gemini 专属的轻量 ACPX Agent，仅提供**多会话并发图片生成/编辑能力**，无多余功能，完全对齐官方 API，开发成本极低。

---

## 2. 仅保留的核心能力
| 能力 | 说明 |
|------|------|
| ✅ 多会话并发 | 同一时间支持多个独立会话并行工作，每个会话对应一个 Gemini `chat` 实例 |
| ✅ 同名会话自动替换 | 新建同名会话时，自动销毁旧会话，替换为全新会话 |
| ✅ 多轮图片修改 | 完全复用 Gemini 原生上下文，修改不需要手动传入上一张图片 |
| ✅ ACPX 协议兼容 | 作为标准 ACPX Agent 接入，支持 `acpx nanobanana` 系列命令 |
| ✅ 流式输出 | 文本/图片生成进度实时返回 |
| ✅ 多模态输入 | 支持图片/文档/音频/视频等 Gemini 支持的所有输入类型 |

### 明确移除的冗余功能
❌ 会话持久化（CLI 重启后会话消失，不需要本地存储）
❌ 历史记录导出/查看
❌ 会话恢复功能
❌ 本地图片历史管理
❌ 复杂终端交互功能

---

## 3. 架构设计（极简）
```
┌─────────────────────────────────────────┐
│          ACPX 接口层                      │
│  会话创建/删除/消息接收/流式响应返回       │
├─────────────────────────────────────────┤
│          会话管理层                      │
│  运行时会话 Map 存储、同名替换逻辑         │
│  每个会话对应一个 Gemini Chat 实例        │
├─────────────────────────────────────────┤
│          Gemini API 适配层                │
│  官方 SDK 调用、请求/响应格式转换、错误处理 │
└─────────────────────────────────────────┘
```

### 核心实现逻辑
- 会话仅在运行时内存中存在，使用简单的 Map 结构存储：`Map<sessionName, GeminiChatInstance>`
- 新建会话时如果名称已存在，直接调用 `map.delete(name)` 销毁旧会话，再存入新的 Chat 实例
- 每个会话的上下文完全由 Gemini Chat 实例维护，不需要额外存储
- 图片生成后直接返回给调用方，不需要本地保存（可选提供临时文件路径）

---

## 4. ACPX 命令设计（仅保留必要命令）
```bash
# 1. 新建会话
acpx nanobanana session new --name <session-name>
# 说明：如果名称已存在，自动销毁旧会话，创建全新会话，无任何提示

# 2. 发送请求（会话内多轮对话）
acpx nanobanana -s <session-name> "<prompt>" [--file <path>...]
# 示例：
acpx nanobanana -s project1 "生成一张赛博朋克风格的猫"
acpx nanobanana -s project1 "把猫改成蓝色"  # 不需要传上一张图片
acpx nanobanana -s project1 --file reference.jpg "参考这个风格修改"

# 3. 删除会话
acpx nanobanana session delete <session-name>

# 4. 列出所有运行中的会话
acpx nanobanana session list
# 返回格式：会话名称 → 创建时间
```

---

## 5. 核心交互逻辑
### 5.1 多会话并发示例
```bash
# 新建会话 A
acpx nanobanana session new --name projectA
# 会话 A 生成图片
acpx nanobanana -s projectA "生成一张猫的图片"
# 会话 A 修改图片（不需要传图）
acpx nanobanana -s projectA "把猫改成蓝色"

# 新建会话 B，与会话 A 完全独立
acpx nanobanana session new --name projectB
# 会话 B 生成完全不同的图片
acpx nanobanana -s projectB "生成一张狗的图片"

# 新建同名会话 A，旧会话 A 自动销毁，替换为新的
acpx nanobanana session new --name projectA
# 新会话 A 上下文是空的，与之前的会话 A 无关
acpx nanobanana -s projectA "生成一张鸟的图片"
```

### 5.2 多轮图片修改逻辑（官方原生）
完全复用 Gemini Chat 上下文，不需要任何额外处理：
- 每个会话对应一个 `client.chats.create()` 实例
- 所有历史消息（包括生成的图片）由 Gemini 自动维护
- 多轮修改直接发文本指令即可，不需要手动传入上一张图片

---

## 6. 错误处理（极简）
| 错误 | 处理方式 |
|------|----------|
| 会话不存在 | 返回错误：`会话 [name] 不存在，请先创建` |
| API Key 配置错误 | 返回错误：`Google API Key 无效，请检查配置` |
| 配额不足 | 返回错误：`API 配额不足，请稍后再试` |
| 输入文件不支持 | 返回错误：`不支持的文件类型：[type]` |
| 内容拦截 | 返回错误：`生成内容不符合安全政策，请调整提示词` |

---

## 7. 开发计划（2周即可完成）
| 阶段 | 周期 | 目标 |
|------|------|------|
| Phase 1 | 1周 | 核心功能实现：<br>1. 官方 Gemini SDK 集成<br>2. 运行时会话管理（Map 存储 + 同名替换）<br>3. 文生图/图生图/多轮修改功能<br>4. 基础错误处理 |
| Phase 2 | 1周 | ACPX 适配：<br>1. ACPX 协议接口实现<br>2. 流式响应支持<br>3. 文件上传处理<br>4. 功能测试 |

---

## 8. 配置说明（极简）
仅需要一个环境变量即可运行：
```bash
export GOOGLE_API_KEY="AIzaSy**************************"
```

可选配置：
```bash
# 默认使用的 Gemini 模型
export NANOBANANA_DEFAULT_MODEL="gemini-3.1-flash-image-preview"
```

---

## 核心优势
- **开发成本极低**：仅需要几百行代码即可实现所有功能
- **无状态易部署**：会话仅在内存中，不需要数据库/本地存储
- **完全对齐官方**：没有多余封装，Gemini 所有原生能力都可直接使用
- **完全符合 ACPX 规范**：无缝接入 openclaw 生态，不需要额外适配
