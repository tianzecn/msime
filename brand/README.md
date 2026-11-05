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

### iOS 测试包（TrollStore）

还没加入 Apple Developer Program，iOS 先打伪签名的 ipa，用 TrollStore 装到测试机上：

```sh
brew install xcodegen cocoapods ldid
~/worktrees/mocai-build/brand/build-trollstore-ipa.sh ~/worktrees/mocai-deps/artifacts
```

脚本先用上游的 `platforms/ios/build-app.sh` 做不签名的真机构建，再用 `ldid` 把 App Group 和钥匙串的 entitlements 写进 App 和键盘扩展。团队 ID 用占位值，TrollStore 不校验它。这个包只能用 TrollStore 装，不能上 App Store，也不能用普通的侧载工具装。

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

## 取值

`brand/mocai.toml` 的 `[values]`：

| 项 | 取值 |
| --- | --- |
| 官网和服务 | `mocai.wangpisi.com`；后端 `api.`、头像 `media.`、可选的下载镜像 `dl.`，都在它下面 |
| 产品标识 | `com.wangpisi.mocai`（iOS 是 `com.wangpisi.mocai.ios`）。首个正式版发布后不能再改，用户要重装 |
| 源码和发版仓库 | `tianzecn/msime`。检查更新读它的 GitHub Releases，Sparkle 的 appcast 挂在它的 `macos-appcast` release 上 |
| Sparkle 公钥 | `sparkle_public_key`，由补丁写进 `platforms/macos/sparkle-public-key.txt` |
| Google 登录 | Cloud 项目 `mocai-ime`（`google_project_number`）。`google_ios_client` 按 bundle ID 登记，`google_server_client` 是「Web 应用」类型的服务端客户端。macOS 用的「桌面应用」客户端不进客户端代码，ID 和密钥只配在墨采云上 |

还是占位值、`audit --release` 不让过的：

- Apple 团队：`apple_team_id`。还没加入 Apple Developer Program，iOS 先打 TrollStore 用的伪签名包。

### Sparkle 私钥

私钥在维护者登录钥匙串里，账户名 `mocai-ime`，另存在仓库 Secret `SPARKLE_ED_PRIVATE_KEY`（GitHub 只写不读）。丢了它，已经装好的墨采就再也收不到更新，所以要另备一份：

```sh
~/worktrees/mocai-deps/Sparkle-2.9.6/bin/generate_keys --account mocai-ime -x ~/Desktop/mocai-sparkle-key.txt
# 存进密码管理器，再删掉这个文件
rm -P ~/Desktop/mocai-sparkle-key.txt
```

## 图片

标志、各平台图标、吉祥物「墨墨」和 66 张表情贴纸都由 [art/generate.py](art/generate.py) 用代码画成 SVG，再用 librsvg 渲染，输出到 `brand/assets/`。目录结构与上游一一对应，`apply` 在规则和补丁之后按相同路径覆盖上游文件。

```sh
brew install librsvg webp
python3 brand/art/generate.py --sheet /tmp/mocai-art   # 重新生成全部图片，另出三张对照图便于目检
```

- 标志：墨色圆角方块、金边，白色笔画是一滴墨和一道回锋。iOS 的四个备选图标只换边色。
- 代码里画的标志（共享设置页的 `MsimeMark`、iOS 的 `MSIMELogo`、macOS 悬浮工具栏、开屏、Google 登录回跳页）由 `mocai.toml` 的补丁换成同一组坐标。坐标由 `generate.py` 算出，测试核对两边一致。改笔画先改 `generate.py`，再更新这几处补丁。
- 贴纸上的字用霞鹜文楷（SIL OFL 1.1），只在生成时用，不随应用分发。字体放在 `MOCAI_FONT_DIR`，缺省 `~/worktrees/mocai-deps/fonts`。
- 这一套是矢量画法，比上游的插画简单。以后换成插画师或图像模型出的图，按 `brand/assets/` 里的文件名和尺寸替换就行，`apply` 不用改。

## GPL 义务

- 修改声明：关于页面写着「基于水杉输入法（MSIME）修改」（第 5 条 a 款），上游的版权行保留。
- 源码：发布二进制时同时提供对应源码和重新构建说明（第 6 条）。公开品牌层分支即可，改写后的树可由它复现。
- 整个安装包按 GPL-3.0-only 授权，不能附加更严的限制。

## 不分发的数据

加加辅助码表（`resources/helpcodes/jiajia_helpcode.txt`）部分条目来自商业软件，GPL 不覆盖它的内容，见 [docs/open-source-release.md](../docs/open-source-release.md)。品牌层把它从引擎和两个平台的打包里去掉。偏好枚举和云同步下标故意保留：下标不能变，已有偏好值也要读得出来。
