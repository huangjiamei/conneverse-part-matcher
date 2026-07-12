# Conneverse Part Matcher（可分发包）

这是一个零依赖的 Python 本地工具，用于对单个 `source_part_info` 执行：

1. eBay MPN、车型 compatibility、车型关键词三路候选检索；
2. 每路最多 5 条，按 eBay `itemId` 去重后全局最多 15 条；
3. MPN 标注、n-gram fitment 判断；
4. 可选的 OpenAI LLM 语义复核。

## 环境要求

- Python 3.10 或更高版本（已使用 Python 3.11 验证）
- eBay Developer Application 的 Client ID 和 Client Secret
- 可选：OpenAI API Key

不需要安装第三方 Python 包。

## 配置

在当前目录复制配置模板：

```powershell
Copy-Item .env.example .env
```

然后编辑 `.env`，至少填写：

```text
EBAY_CLIENT_ID=...
EBAY_CLIENT_SECRET=...
```

只有启用 LLM 语义复核时才需要填写 `OPENAI_API_KEY`。

## 启动 Web 页面

在本目录运行：

```powershell
python -m end_to_end_part_matcher.web --open
```

默认地址为 <http://127.0.0.1:8000/>。该服务器只适合本地使用，不要直接暴露到公网。

## CLI 示例

```powershell
python -m end_to_end_part_matcher.pipeline `
  --source-json (Get-Content -Raw .\example-source.json) `
  --no-llm `
  --pretty
```

去掉 `--no-llm` 会对 n-gram 判定为 `review` 的候选调用 OpenAI。

## 包内容

- `end_to_end_part_matcher/`：检索、匹配、LLM 和 Web 页面
- `algorithms/fitment/`：n-gram 与 fitment decision engine
- `data/.../part_desc_to_category.json`：零件描述到 eBay 类目的映射
- `.env.example`：无真实密钥的配置模板
- `example-source.json`：示例输入

本包不包含原项目的 `.env`、真实 API Key、Git 历史、缓存或完整测试数据。
