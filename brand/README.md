# 墨采输入法的品牌层

墨采输入法是水杉输入法（MSIME）的修改版，按 GPL-3.0-only 发布。这个目录让墨采能持续合并上游：分支上只多 `brand/` 和 `scripts/test-brand-layer.py` 这些新文件，改名、换标识和换服务地址都在打包前由脚本完成。

## 用法

```sh
# 在品牌层分支上：只读，看每条规则命中多少处，补丁锚点是否都对得上
python3 brand/apply.py plan

# 打包：取一棵一次性的树（游离 HEAD 或 mocai/build* 分支），改写后在那棵树里构建
git worktree add --detach ~/worktrees/mocai-build mocai/brand-layer
python3 brand/apply.py apply --root ~/worktrees/mocai-build
python3 brand/apply.py audit --root ~/worktrees/mocai-build

# 正式发布前：占位值还在就不通过
python3 brand/apply.py audit --root ~/worktrees/mocai-build --release
```

`apply` 不是幂等的，只接受干净的树，也拒绝在品牌层分支上运行。要重来就 `git reset --hard && git clean -fd` 那棵一次性的树。

## 合并上游

```sh
git fetch origin develop
git merge origin/develop          # 只有新文件，不会冲突
python3 scripts/test-brand-layer.py
```

测试失败说明上游动了补丁锚点，或者某条规则不再命中，按报错改 `brand/mocai.toml`。

## 规则的边界

| 类别 | 处理 |
| --- | --- |
| 用户看得到的名字（水杉、Metasequoia IME、MSIME.app） | 替换 |
| macOS 和 iOS 的系统身份（bundle id、App Group、钥匙串、URL scheme、团队、Google 客户端） | 替换 |
| 服务地址（api、media、dl、官网、更新检查、反馈和源码链接） | 替换 |
| 内部名称（`msime_client_*`、`MSIME*` 类名、crate 名、Java 包名） | 不动，用户看不到，改了只会让合并上游冲突 |
| Android、Windows、Linux、鸿蒙的系统身份 | 暂不动，这些平台不在首发范围。Android 的 JNI 符号也用 `app.msime`，只换字符串会编不过 |
| 许可证、第三方声明、哈希锁定的资源和插件 | 不动，GPL 和各数据源的条款要求保留，改了校验也过不去 |
| 按 SHA-256 固定的资源包下载（`releases/download`） | 仍从上游下载 |

## 上线前要换掉的占位值

`brand/mocai.toml` 的 `[values]` 里，`.invalid` 域名保证解析不到，开发版不会把默认开启的使用统计发给别人。

- 域名：`site_host`、`api_host`、`media_host`、`download_host`、`source_url`
- 发版仓库：`release_repo`。检查更新读它的 GitHub Releases，Sparkle 的 appcast 挂在它的 `macos-appcast` release 上
- Sparkle 公钥：用 Sparkle 的 `generate_keys` 生成墨采自己的一对，公钥写进 `platforms/macos/sparkle-public-key.txt`（在品牌层分支上直接改这个文件），私钥只放发版环境
- 产品标识：`identifier_root`、`keychain_root`。首个正式版发布后再改，用户就要重装
- Apple 团队：`apple_team_id`
- Google 登录：`google_ios_client`、`google_server_client`

## GPL 义务

- 修改声明：关于页面写着「基于水杉输入法（MSIME）修改」（第 5 条 a 款），上游的版权行保留。
- 源码：发布二进制时同时提供对应源码和重新构建说明（第 6 条）。公开品牌层分支即可，改写后的树可由它复现。
- 整个安装包按 GPL-3.0-only 授权，不能附加更严的限制。

## 不分发的数据

加加辅助码表（`resources/helpcodes/jiajia_helpcode.txt`）部分条目来自商业软件，GPL 不覆盖它的内容，见 [docs/open-source-release.md](../docs/open-source-release.md)。品牌层把它从引擎和两个平台的打包里去掉。偏好枚举和云同步下标故意保留：下标不能变，已有偏好值也要读得出来。
