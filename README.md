# 新词雷达 · 免费公开版

在电脑关机后继续发现新 AI 模型、新工具和版本更新。GitHub Actions 每两小时采集一次，每天北京时间 **09:17** 汇总中文日报。

这是 [zhuyansen/new-term-radar](https://github.com/zhuyansen/new-term-radar) 的免费用途分支，保留 MIT 许可。公开命令和工作流不调用付费模型、X 数据服务、SEO 搜索量服务或邮件服务。

## 阅读日报

[打开最新日报](https://github.com/windspoor/new-term-radar-free/blob/radar-data/latest.md) · [历史日报](https://github.com/windspoor/new-term-radar-free/tree/radar-data/reports) · [运行状态](https://github.com/windspoor/new-term-radar-free/actions/workflows/radar.yml)

在本仓库切换到 **radar-data** 分支：

- `latest.md`：过去 24 小时的最新发现，每次采集后更新。
- `reports/YYYY-MM-DD.md`：每天 09:17 生成的历史日报。
- `latest.json`：可供程序使用的报告。

Actions 的 **Free new-term radar** 页面也会显示每次运行的报告摘要。首次部署或修改采集代码时会立即生成一份日报。日报保留近 30 天；Git 历史仍可用于追溯。

## 监控内容

- Hugging Face：Qwen、DeepSeek、Kimi、MiniMax、GLM、Mistral、Google、Meta、FLUX、LTX、Wan、NVIDIA 等团队新上传的公开模型。官方发布不设热度门槛，同一团队的模型尺寸与量化版本合并。
- GitHub：指定 AI 团队新建的公开仓库，指定工具的 Releases，近 14 天达到 20 星的公开 AI 项目。
- 免费补充信号：HF 热门榜、跨平台证据、按实际采集间隔计算的涨星速度。
- RSS/Atom：OpenAI、Hugging Face、Google AI、Simon Willison 的公开动态。

同一工具一天内的多个版本更新会合并展示。发布时间与首次发现时间分别记录；“规则分”只用于阅读排序，不代表搜索量、准确率或投资价值。

首次运行补录近 48 小时的官方发布，以及近两周达到门槛的 AI 仓库，并为其他项目建立基线。以后只报告新发现，失败的来源保持上次成功时间，恢复后补采。没有更新时会明确显示暂无新记录。

## 免费运行与隐私

云端只允许在**公开仓库**的 `ubuntu-latest` 标准运行器上运行。采集使用 Python 标准库，无额外云数据库、付费密钥、Actions 附件或缓存。数据写入独立 `radar-data` 分支；标准公开仓库运行器目前免费，平台政策可能变化。

`config.public.json` 只接受代码中批准的公共来源和有限的数量设置。私人网站、sitemap、Search Console、广告账号、已覆盖关键词、邮箱和密钥配置会被拒绝。GitHub 请求仅采集公开数据，凭证只发送到 `api.github.com`。错误日志只显示类别或 HTTP 状态码。

发布脚本只上传生成的报告与公共状态，绝不扫描并上传整个工作目录。`.env*`、授权文件、私人配置和本地运行结果不提交。

不设置 `AISA_API_KEY`、`GOANYAPI_API_KEY` 或 `RESEND_API_KEY`。即使环境中已有这些密钥，免费 CLI 也不会使用。上游库保留用于兼容测试，旧的付费 CLI 没有公开命令入口。

## 本地运行

Python 3.10 或更新版本，无运行时安装依赖。

```bash
python -m radar collect
python -m radar daily
python -m radar daily --report-only
```

默认读取 `config.public.json`，公共历史写入 `state/free`，报告写入 `reports`。`sources` 是 `collect` 的兼容别名，`free` 是 `daily` 的兼容别名。旧的私人 `config.json` 不会被读取。

## 云端维护

工作流默认开启，支持手动运行 `daily` 或 `collect`。两小时采集与 09:17 日报分别调度，使用北京时间 UTC+8。

GitHub 定时任务可能延迟或漏跑，不能保证即时提醒。日报有来源健康状态和最近成功时间，后续运行会补采。公开仓库超过 60 天没有活动可能停用定时任务；本版持续提交公共采集历史，并保留手动运行入口。

如果来源列表需要扩展，请先核实它是公共团队/官方页面，再同时更新 `radar/free_sources.py` 的来源白名单与公开配置。不要添加你自己的站点或私人监控目标。

## 测试

```bash
pip install pytest markdown
python -m pytest -q tests
```

测试覆盖免费调用隔离、私人配置拒绝、凭证目的地、基线、漏跑补采、去重、涨星计算、RSS、Markdown、历史恢复和发布文件白名单。上游逻辑测试也保留，且不访问网络。

原项目说明归档在 `docs/UPSTREAM_README.md` 和 `docs/UPSTREAM_README.zh-CN.md`，其中付费方案的说明不适用于本分支。
