# Agent Note: iOS 候选栏换字后只布局一次

Status: implemented

## Problem

用户反馈 iOS 上打字明显不如微信输入法跟手。在 iOS 模拟器（iPhone 17 Pro Max，iOS 27.0，Release 配置）里跑 `KeystrokeLatencyTests` 并对测试宿主采样：主线程上 `renderCandidateStrip` 约占四分之一，其中九成多在 `updateCandidateButton`；它里面 `-[UIView _updateConstraintsAtEngineLevelIfNeeded…]` 占 46%，`UIButton` 的 `setConfiguration` 占 11%。

原因是每个换了字的词块在 `updateCandidateButton` 里各自 `button.layoutIfNeeded()`。词块都在同一棵视图树里、共用一个约束引擎，每次调用都让引擎为整条候选栏重新求解，一页换了几个词块就重复几遍。这次 `layoutIfNeeded` 是[所有平台的候选立即呈现](../simplification/2026-10-30-candidate-immediate-presentation.md)加的：被手指按过的词块再套用 configuration 时，UIKit 会给标题挂 0.25 s 的 `CATransition` fade，所以换字和随之的布局都要放在 `UIView.performWithoutAnimation` 里做完。

[主线程绘制预算](../bug-fix/2026-10-18-ios-keystroke-render-budget.md)记过一次「词块布局保留试验」：真机整键 P95 没有稳定改善，已撤回。那次早于上面这个逐词块布局，不是同一处开销，但它说明候选栏上的局部改动在真机上可能看不出来，下面的模拟器数字不能外推到真机。

## Decision

`updateCandidateButton` 仍在 `performWithoutAnimation` 里套用 configuration、行数和宽度约束，但不再自己布局。`renderCandidateStrip` 在所有词块换完（含每个词块之后的 `CandidateStripSizing.apply`）之后，只要有词块换过字，就在一个 `performWithoutAnimation` 里对 `candidateStack` 调一次 `layoutIfNeeded`。

这次改动最初基于横向栈直接排列词块的代码，只合并布局会改变 UIKit 解出来的宽度：`SymbolCandidateChipTests.testSymbolRowsStayAsNarrowAsTheirSymbolWithGlossesOn` 失败，`UISV-text-width-disambiguation` 约束从 0 条变成 9 条，栈宽 716.67 变成 894，`㎥` 词块宽 39.33 变成 166。当时把词块的水平拥抱优先级设成 990 压住它（观察到这类约束是 952、956，宽度约束是 998、999）。合并 develop 之后，[候选文字宽度与横向栈隔离](../bug-fix/2026-11-05-ios-candidate-width-solver.md)让横向栈只排列容器，这组约束不再生成，990 随之去掉：同一个原生库，上游 Swift、合并版（带 990）、去掉 990 三臂跑同一用例，20 行的宽度和位置逐字节相同，消歧约束都是 0 条，栈宽都是 716.67、824.33。

## Alternatives considered

- **跳过内容没变的词块**：已经有了（`candidateButtonInputs`），但打字时每键大多数词块都换字，省不下这部分布局。
- **其余保持原几何的办法**：合并 develop 之前，两遍布局、中间 `setNeedsUpdateConstraints`、提前设置 `numberOfLines`、重写 `updateConstraints` 都试过，符号词块用例仍然失败。
- **保留拥抱优先级 990 作为保险**：万一包装层没有覆盖到别的情况，它还能压住消歧约束。但三臂对照里它没有任何作用，留着就多一处依赖 UIKit 内部优先级值的代码，所以去掉；这个行为仍由符号词块用例守住。
- **候选栏改用自绘或 `UICollectionView` 复用**：能连 `setConfiguration` 的开销一起去掉，但要重写词块的皮肤、释义、标记和读屏，改动面远大于这次；要等真机数据说明值得再做。

## Consequences

合并 develop（`df30a5696d`）之后重测。模拟器 Release 配置，同一个原生库，只换这一处 Swift，不挂采样器，每轮重新链接，改前改后各 4 次交替（`p50 / p95`，ms，取 4 次的中位数）：

| 用例 | 改前 | 改后 |
|---|---|---|
| 全拼 `nihao` 每键（同步，含布局） | 3.01 / 8.23 | 2.40 / 7.96 |
| 九键 `64426` 每键 | 3.33 / 7.24 | 3.17 / 6.39 |
| 全拼 `yi`（候选多） | 6.12 / 6.95 | 4.93 / 6.07 |
| 九键长句整键 | 6.87 / 12.23 | 6.17 / 11.22 |
| 异步九键 40 ms 节奏，最后一键到画完 | 11.94 / 14.69 | 11.55 / 14.78 |
| 异步九键 70 ms 节奏，最后一键到画完 | 20.35 / 22.52 | 20.45 / 22.59 |
| 全拼空格 | 2.67 / 6.47 | 2.98 / 6.93 |
| 九键空格 | 2.06 / 2.31 | 2.13 / 2.73 |
| 九键长句只算桥接（对照） | 2.99 / 8.21 | 2.94 / 8.11 |

前四行除九键 `64426` 的 p50 有重叠外，改后每一次的 p50 和 p95 都低于改前任何一次，只算桥接的对照组不变。不利的结果如实保留：异步节奏下看不出改善，这两个用例每轮只有 12 个样本，还带 1 ms 粒度的轮询；空格变慢了，九键空格改后 4 次的 p50 都高于改前（2.12–2.42 对 2.02–2.08），全拼空格只差一点重叠（2.82–3.11 对 2.64–2.85），合并前也看到过同方向的 0.1–0.2 ms，应当是真实的小代价，原因没有查。

合并前（基线 `e270741ee9`）同样的对照里，全拼 `yi` 是 7.36 / 26.37 → 4.69 / 5.96，全拼 `nihao` 是 3.44 / 11.07 → 2.67 / 8.61，九键 `64426` 是 4.19 / 9.64 → 3.50 / 5.72。合并后差距变小，尤其是 `yi` 的 p95，改前已经是 7 ms 左右；这应当主要来自[候选文字宽度与横向栈隔离](../bug-fix/2026-11-05-ios-candidate-width-solver.md)，没有单独验证。

这组数字的适用范围有限：跑在 M4 Max 上；测试宿主挂随包的 safetensors 模型，不是产品的 ONNX；`textDocumentProxy` 不连真实文档；连续敲键，CPU 和缓存一直是热的。没有在真机上量，也没有测过微信输入法。

这组基准看不到真人节奏下的开销，连续敲键时释义的防抖从不触发。按 150–300 ms 的节奏走异步路径，释义开（iOS 默认）比释义关时每键周期的主线程占用多约一倍（p50，九键 7.4 → 14.9 ms，全拼 6.9 → 12.7 ms）：按键后约 135 ms 释义到达（120 ms 防抖加查询），再做一次完整 `render`。其中九键 60 键里有 24 键候选词块横移（幅度中位数 33 pt），全拼 `nihao` 没有横移。这次没有处理：防抖是有意的设计，改法要在响应时机和词块宽度之间取舍，应先在真机上比较释义开关。

回归（Debug，全新模拟器，合并 develop 之后）：键盘 1369 项（11 跳过）、服务 125 项、共享 139 项，全部通过。合并之前唯一的失败是 `KeyboardViewTeardown.swift:8` 的 `NSISEngine: Not possible to remove variable`，[重排器内存笔记](../bug-fix/2026-11-02-ios-keyboard-reranker-memory.md)记过这个销毁断言；当时同一个原生库只换这处 Swift，`NineKeyKeyboardTests` 交替跑（每臂 6 遍共 708 个用例），原代码触发 5 次，改后 1 次，没有其他错误，只能说明这次改动没有增加它。[键盘根视图改用普通 UIView](../bug-fix/2026-11-05-ios-keyboard-plain-root-view.md) 删掉了 `KeyboardViewTeardown`，合并后这一轮没有再出现；只跑了一轮，不能证明断言已经根除。`verify-local.sh --quick` 的结果见[压缩路径页笔记](2026-11-06-compact-path-page-shared-strings.md)：只有 Android 阶段因本机环境失败，`shared apple bridge` 通过，macOS 原生编译被跳过。

代价：`updateCandidateButton` 返回时词块的几何还是旧的，要等整页换完。`renderCandidateStrip` 里目前没有在这之间读词块 `bounds`、`frame` 的代码，`CandidateStripSizing.apply` 只改约束，`naturalContentHeight` 读的是固有尺寸。被手指按过的词块换字不渐变这一项，进程内测试触发不了，原来靠模拟器录屏验收，这次没有重录；`InlineCompositionDefaultTests.testTypingRedrawsTheStripWithoutAnimations` 只守住按键重画候选条不带动画。
