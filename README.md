# astrbot_plugin_douyin_ai_bot

抖音 AI Bot 插件 for [AstrBot](https://github.com/AstrBotDevs/AstrBot) — 让你的 AI 角色在抖音评论区"活"起来。

## ✨ 功能

### 💬 评论与互动

- **评论自动回复** — 轮询评论通知，自动生成 AI 回复
- **@ 通知回复** — 有人在评论区 @Bot 时自动收到并回复
- **视频上下文** — 自动获取被评论视频的信息，辅助生成更精准的回复
- **联网查询** — 结合 LLM 能力按需回复

### 🧠 记忆与人格

- **语义记忆** — 对话记录存储与关键词检索，回复时自动注入相关记忆
- **好感度系统** — 陌生人 → 粉丝 → 熟人 → 好友 → 主人，不同等级不同语气；辱骂自动拉黑
- **用户画像** — 记录用户昵称、互动偏好等
- **心情系统** — 每日随机心情 + 节日彩蛋

### 🎯 主动行为

- **主动刷视频** — 定时刷抖音推荐流，LLM 分析评价视频
- **`/dy 主动` 手动触发** — 命令式立刻触发一次主动刷视频
- **智能互动** — 根据 LLM 评分自动点赞 / 评论 / 关注
- **分享链接解析** — 识别群里的抖音链接，自动发送解析卡片

### 🛠️ 运维与安全

- **LLM 熔断保护** — 单条重试 3 次放弃，全局连续 5 次失败冷却 5 分钟
- **黑名单管理** — 手动拉黑，黑名单用户不调 LLM 不花钱
- **Cookie 管理** — 支持通过命令设置和验证 Cookie

## 📦 安装

在 AstrBot WebUI 里通过网页单独安装。

或手动安装：

```bash
cd AstrBot/data/plugins
git clone https://github.com/qsbb/astrbot_plugin_douyin_ai_bot
```

### Python 依赖

插件内的 Python 依赖由 [requirements.txt](requirements.txt) 管理：

- `aiohttp>=3.9` — 异步 HTTP 请求
- `Pillow>=9.0` — 图片处理（预留）

## ⚙️ 配置

安装后在 WebUI 插件配置页面填写：

| 配置项 | 必填 | 说明 |
|---|---|---|
| `DOUYIN_COOKIE` | ✅ | 抖音 Cookie 字符串（浏览器登录后 F12 复制） |
| `LLM_PROVIDER_ID` | ✅ | 选择用于回复的 LLM 模型 |
| `OWNER_UID` | 推荐 | 主人的抖音 UID（好感度特殊处理） |
| `OWNER_NAME` | 推荐 | 主人名称（用于 prompt） |
| `ENABLE_REPLY` | 可选 | 启用评论自动回复，默认 true |
| `ENABLE_AFFECTION` | 可选 | 启用好感度系统，默认 true |
| `ENABLE_MEMORY` | 可选 | 启用语义记忆，默认 true |
| `ENABLE_MOOD` | 可选 | 启用心情系统，默认 true |
| `ENABLE_PROACTIVE` | 可选 | 启用主动刷视频，默认 false |
| `ENABLE_SHARE_PARSE` | 可选 | 启用分享链接解析，默认 false |
| `POLL_INTERVAL` | 可选 | 评论轮询间隔（秒），默认 30 |
| `REPLY_PROBABILITY_PERCENT` | 可选 | 回复概率百分比，默认 80% |
| `CUSTOM_SYSTEM_PROMPT` | 可选 | 自定义系统提示词 |
| `CUSTOM_REPLY_INSTRUCTION` | 可选 | 回复评论的补充提示词 |

### Cookie 获取方式

1. 在浏览器中打开 [抖音官网](https://www.douyin.com) 并登录
2. 按 F12 打开开发者工具
3. 进入 Application → Cookies → `www.douyin.com`
4. 复制全部 Cookie 字符串
5. 在 AstrBot 中发送 `/dy Cookie <粘贴Cookie>` 或填入插件配置页

💡 Cookie 也可以直接在插件配置页的 `DOUYIN_COOKIE` 字段中填写。

### 缺失功能时的退化行为

- **没有 Cookie** — 评论回复、主动行为等功能不可用
- **没有 LLM Provider** — 无法生成回复，仅记录评论通知
- **主动行为未开启** — 仅被动回复评论，不会主动刷视频

## 🎮 命令

| 命令 | 说明 |
|---|---|
| `/dy 状态` | 查看运行状态 |
| `/dy 启动` | 启动 Bot |
| `/dy 停止` | 停止 Bot |
| `/dy Cookie <cookie>` | 设置抖音 Cookie |
| `/dy 主动` | 立刻触发一次主动刷视频 |
| `/dy 好感 [UID]` | 查看好感度排行 / 查询 |
| `/dy 记忆 <关键词>` | 搜索记忆 |
| `/dy 心情` | 查看今日心情 |
| `/dy 拉黑 <UID>` | 手动拉黑用户 |
| `/dy 黑名单` | 查看黑名单 |
| `/dy 日志` | 查看主动行为日志 |
| `/dy 统计` | 查看统计数据 |
| `/dy 帮助` | 查看帮助 |

## 🏗️ 好感度等级

| 等级 | 分数 | 语气风格 |
|---|---|---|
| 💖 主人 | 100 | 撒娇宠溺 |
| ✨ 好友 | > 50 | 温暖真诚 |
| 😊 熟人 | > 30 | 轻松调侃 |
| 👋 粉丝 | > 10 | 友好温和 |
| 🌙 陌生人 | 0-10 | 礼貌简洁 |
| 🖤 厌恶 | < 0 | 极简冷淡 |

## 📁 数据存储

插件数据存储在 `data/plugin_data/astrbot_plugin_douyin_ai_bot/` 目录下，更新插件不会丢失数据。

## ⚠️ 风险提示

- 使用本插件意味着 Bot 会使用你登录的抖音账号进行自动化操作（评论、点赞、关注等），**存在账号被风控的风险**，请谨慎调节轮询间隔和主动行为频率
- 建议不要用主号测试，必要时准备小号
- Cookie 可能过期，请定期检查/刷新

## 💖 支持这个项目

如果这个插件帮到你了，欢迎到 [GitHub 仓库](https://github.com/qsbb/astrbot_plugin_douyin_ai_bot) 点个 ⭐

插件还在持续更新功能，欢迎通过 [Issues](https://github.com/qsbb/astrbot_plugin_douyin_ai_bot/issues) 反馈 bug、提建议或者请求新功能。

## 🔗 相关

- [AstrBot 文档](https://docs.astrbot.app/)
- [问题反馈](https://github.com/qsbb/astrbot_plugin_douyin_ai_bot/issues)
- [B站 AI Bot 插件](https://github.com/chenluQwQ/astrbot_plugin_bilibili_ai_bot) — 本插件的设计参考

## 💕 致谢

感谢 [chenluQwQ](https://github.com/chenluQwQ) 的 [B站 AI Bot 插件](https://github.com/chenluQwQ/astrbot_plugin_bilibili_ai_bot) 提供的设计参考。

感谢 AstrBot 社区各位群友的帮助与支持。

## 📄 License

MIT
