# Agent Note: 墨采输入法用打包前改写的品牌层跟踪上游

Status: implemented

## Problem

墨采输入法是水杉输入法的修改版，先发 macOS 和 iOS，后端自建。2026-11-04 按 develop e38aae4d5b 盘点，不含文档和测试：「水杉」在 509 个文件里约 1400 行，`app.msime.` 在 800 个文件里约 2200 行，服务地址分散在 Rust、Swift、Java、C++ 和 TypeScript 里。上游近 30 天有 10455 个提交。把改名直接提交进分支，之后每次合并上游都要在这些行上解冲突，上游新加的文案还会继续带着旧名字进来。

上游没有现成的品牌机制。版本表 `shared/contracts/editions.json` 只管系统身份，`scripts/test-editions.py` 还强制新版本沿用 `app.msime.*` 前缀；界面文案不归它管。

## Decision

分支上只放新文件：`brand/mocai.toml` 是规则和取值，`brand/apply.py` 把它们套到一棵一次性的树上再构建，`scripts/test-brand-layer.py` 是回归测试。合并上游没有冲突，上游新增的文案由同一套规则替换，漏掉的由 `apply.py audit` 报出来。

几条边界：

- 用户看得到的名字、macOS 和 iOS 的系统身份、服务地址要换；`msime_client_*`、`MSIME*` 类名、crate 名这些内部名称不换，用户看不到。
- Android、Windows、Linux、鸿蒙的系统身份暂不动。Android 的 JNI 符号也用 `app.msime`，只换字符串会编不过。轮到这些平台时再扩规则。
- 许可证、第三方声明、带哈希的资源和插件不改写。按 SHA-256 固定的资源包继续从上游 GitHub 发布下载。
- 占位域名用 `.invalid`（RFC 6761 保证解析不到），不用 `mocai.app` 这类可能属于别人的真实域名：使用统计默认开启，开发版会真的发请求。`audit --release` 在还有占位值时不通过。
- 名字写成八进制或 Unicode 转义的生成文件（`LinuxEdition.h`、`msime_edition.h`），文本规则替换不到，改写后重跑它们的生成器，转义形式进审计。

验证方式是用上游自己的契约检查：在改写前后的两棵树上各跑一遍 `scripts/test-*.py`，只有改写后才失败的才算品牌层的问题。这组检查抓到过规则漏掉的正则写法标识和转义文案。

加加辅助码表部分条目来自商业软件，GPL 不覆盖它的内容（`docs/open-source-release.md`），墨采不分发。云同步下标表 `MSIMECloudHelpcodeSchemas` 和按它排列的 `helpcodeSchemeTitles` 保留，删一项后面全部错位；`scripts/test-settings-label-parity.py` 只对这张表和 Linux 菜单豁免 `jiajia` 一项。

## Alternatives considered

- 硬分叉，把改名直接提交。最强的理由是简单：没有脚本，代码里看到的就是发布的，IDE 和 grep 都不用多想一层。不用，是因为每月上万个提交落在这几百个文件上，几次合并之后实际上就放弃了上游的修复。
- 只加一个新版本到版本表。这是上游区分版本的正路，改动最小，系统身份也有校验。但它管不到界面文案和服务地址，校验规则又要求 `app.msime.*` 前缀，只能解决一小部分。
- 把品牌做成上游的运行时配置。长远看最干净，但要改六个平台上千处调用，上游也没有这个需求，不能指望合进去。

## Consequences

- 平时看到的代码是水杉的，构建和调试墨采都要在改写后的树里做；`apply` 不幂等，重来要先 `git reset --hard` 那棵树。
- 上游动了补丁锚点时，`scripts/test-brand-layer.py` 在合并后就失败，不会拖到发版。
- 引擎的测试和 golden 基准要在未改写的树上跑：改写后的树删掉了加加码表。
- macOS 输入模式名只有简体和英文两份。系统首选繁体时，输入源接口给的是英文那份，多放一份 `zh-Hant.lproj` 也不读（2026-11-05 实测），所以英文名和中文名同样要改。这份英文名曾经漏改，墨采的模式和水杉的并排叫 `Metasequoia …`，添加输入法时分不清。
- 词库更新继续读上游 msime-dictionary 的发布，来源只由运行时校验，发布前要决定镜像或关闭自动更新。
- 图片由 `brand/art/generate.py` 生成到 `brand/assets/`，`apply` 按相同路径覆盖上游文件，目标不存在就失败。标志在代码里另画了五份（TSX、Swift、AppKit、开屏样式、Google 回跳页），由补丁换成生成器算出的同一组坐标，测试核对一致。贴纸和吉祥物是代码画的矢量图，比上游的插画简单，以后换图只替换文件。
