# Agent Note: iOS 候选栏换字后只布局一次

Status: implemented

## Problem

用户反馈 iOS 上打字明显不如微信输入法跟手。在 iOS 模拟器（iPhone 17 Pro Max，iOS 27.0，Release 配置）里跑 `KeystrokeLatencyTests` 并对测试宿主采样：主线程上 `renderCandidateStrip` 约占四分之一，其中九成多在 `updateCandidateButton`；它里面 `-[UIView _updateConstraintsAtEngineLevelIfNeeded…]` 占 46%，`UIButton` 的 `setConfiguration` 占 11%。

原因是每个换了字的词块在 `updateCandidateButton` 里各自 `button.layoutIfNeeded()`。词块都在同一棵视图树里、共用一个约束引擎，每次调用都让引擎为整条候选栏重新求解，一页换了几个词块就重复几遍。这次 `layoutIfNeeded` 是[所有平台的候选立即呈现](../simplification/2026-10-30-candidate-immediate-presentation.md)加的：被手指按过的词块再套用 configuration 时，UIKit 会给标题挂 0.25 s 的 `CATransition` fade，所以换字和随之的布局都要放在 `UIView.performWithoutAnimation` 里做完。

[主线程绘制预算](../bug-fix/2026-10-18-ios-keystroke-render-budget.md)记过一次「词块布局保留试验」：真机整键 P95 没有稳定改善，已撤回。那次早于上面这个逐词块布局，不是同一处开销，但它说明候选栏上的局部改动在真机上可能看不出来，下面的模拟器数字不能外推到真机。

## Decision

`updateCandidateButton` 仍在 `performWithoutAnimation` 里套用 configuration、行数和宽度约束，但不再自己布局。`renderCandidateStrip` 在所有词块换完（含每个词块之后的 `CandidateStripSizing.apply`）之后，只要有词块换过字，就在一个 `performWithoutAnimation` 里对 `candidateStack` 调一次 `layoutIfNeeded`。

只合并布局会改变 UIKit 解出来的宽度，所以 `makeCandidateButton` 同时把词块的水平拥抱优先级设为 990。`SymbolCandidateChipTests.testSymbolRowsStayAsNarrowAsTheirSymbolWithGlossesOn` 在只合并布局时失败：原代码 `UISV-text-width-disambiguation` 约束 0 条、栈宽 716.67、`㎥` 词块宽 39.33；只合并后这类约束 9 条、栈宽 894、`㎥` 宽 166。观察到这类约束的优先级是 952、956，宽度约束是 998、999，990 落在中间。加上之后，该用例 20 行的宽度和位置与原代码逐字节相同。

## Alternatives considered

- **跳过内容没变的词块**：已经有了（`candidateButtonInputs`），但打字时每键大多数词块都换字，省不下这部分布局。
- **其余保持原几何的办法**：两遍布局、中间 `setNeedsUpdateConstraints`、提前设置 `numberOfLines`、重写 `updateConstraints` 都试过，符号词块用例仍然失败。
- **候选栏改用自绘或 `UICollectionView` 复用**：能连 `setConfiguration` 的开销一起去掉，但要重写词块的皮肤、释义、标记和读屏，改动面远大于这次；要等真机数据说明值得再做。

## Consequences

模拟器 Release 配置，同一个原生库，只换这一处 Swift，不挂采样器，每轮重新链接，改前改后各 4 次交替（`p50 / p95`，ms，取 4 次的中位数）：

| 用例 | 改前 | 改后 |
|---|---|---|
| 全拼 `nihao` 每键（同步，含布局） | 3.44 / 11.07 | 2.67 / 8.61 |
| 九键 `64426` 每键 | 4.19 / 9.64 | 3.50 / 5.72 |
| 全拼 `yi`（候选多） | 7.36 / 26.37 | 4.69 / 5.96 |
| 九键长句整键 | 6.79 / 13.61 | 6.08 / 11.55 |
| 异步九键 40 ms 节奏，最后一键到画完 | 10.48 / 13.91 | 9.11 / 11.55 |
| 异步九键 70 ms 节奏，最后一键到画完 | 15.02 / 20.49 | 15.37 / 19.51 |
| 全拼空格 | 3.21 / 8.38 | 3.32 / 7.31 |
| 九键空格 | 2.25 / 2.70 | 2.43 / 2.75 |
| 九键长句只算桥接（对照） | 2.44 / 7.42 | 2.49 / 7.65 |

前四行改后每一次的 p50 和 p95 都低于改前任何一次，只算桥接的对照组不变。两处不利的结果如实保留：70 ms 节奏下最后一键到画完看不出改善，这个用例每轮只有 12 个样本，还带 1 ms 粒度的轮询；空格的 p50 高了 0.1–0.2 ms，各次有重叠，没有查是否真实。

这组数字的适用范围有限：跑在 M4 Max 上；测试宿主挂随包的 safetensors 模型，不是产品的 ONNX；`textDocumentProxy` 不连真实文档；连续敲键，CPU 和缓存一直是热的。没有在真机上量，也没有测过微信输入法。

这组基准看不到真人节奏下的开销，连续敲键时释义的防抖从不触发。按 150–300 ms 的节奏走异步路径，释义开（iOS 默认）比释义关时每键周期的主线程占用多约一倍（p50，九键 7.4 → 14.9 ms，全拼 6.9 → 12.7 ms）：按键后约 135 ms 释义到达（120 ms 防抖加查询），再做一次完整 `render`。其中九键 60 键里有 24 键候选词块横移（幅度中位数 33 pt），全拼 `nihao` 没有横移。这次没有处理：防抖是有意的设计，改法要在响应时机和词块宽度之间取舍，应先在真机上比较释义开关。

回归（Debug，全新模拟器，无并行负载）：键盘 1268 项（11 跳过）、服务 124 项、共享 139 项，唯一失败是 `KeyboardViewTeardown.swift:8` 的 `NSISEngine: Not possible to remove variable`。这是[重排器内存笔记](../bug-fix/2026-11-02-ios-keyboard-reranker-memory.md)记过的销毁断言，#10859 之后仍会触发，报错落在控制器释放时碰巧在跑的用例上。同一个原生库只换这处 Swift，`NineKeyKeyboardTests` 交替跑（每臂 6 遍共 708 个用例）：原代码触发 5 次，改后 1 次，没有其他错误。次数太少，不能说明这次改动减少了它，只能说明没有增加。`verify-local.sh --quick` 退出码 1，只有三个 Android 阶段因本机缺 `javac` 和 API 35 失败，`shared apple bridge` 通过，macOS 原生编译被跳过（结果见[压缩路径页笔记](2026-11-06-compact-path-page-shared-strings.md)）。

代价：拥抱优先级依赖观察到的 UIKit 内部优先级值，消歧约束本身可能还在，系统升级后可能变化，靠符号词块用例守住。被手指按过的词块换字不渐变这一项，进程内测试触发不了，原来靠模拟器录屏验收，这次没有重录；`InlineCompositionDefaultTests.testTypingRedrawsTheStripWithoutAnimations` 只守住按键重画候选条不带动画。
