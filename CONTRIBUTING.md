# 参与贡献

感谢你愿意为 aicbbuu network tools 花时间。这份文档说明怎么把改动
安全地合进来，以及哪些约定是这个项目特有的。

## 环境准备

需要 **Python 3.10 或更高版本**。项目只依赖 PySide6，测试不依赖
pytest。

```bash
git clone https://github.com/aicbbuu/aicbbuu-network-tools.git
cd aicbbuu-network-tools
pip install PySide6
```

打包还需要 `pyinstaller`：

```bash
pip install pyinstaller
```

## 跑测试

三个套件，**改动提交前必须全绿**：

```bash
python tests/run_tests.py       # 核心层
python tests/test_qt_ui.py      # UI 层
python tests/test_qt_window.py  # 窗口交互
```

它们不依赖 pytest，直接 `python` 就能跑。三个套件都在子进程 + 超时下
运行，所以即使 GUI 卡住也不会挂住整个流程。

## 这个项目的特有约定

这几条都有明确的代价，改动前请先读完对应的说明。

### 测「按钮能用」就点按钮，测「能画」就真画一次

**这两条曾经各漏掉一个线上故障，而测试当时全绿。**

**① 直接调被测对象的方法，等于只测了方法本身。**

主题切换按钮点不动的那次：

```python
self.titlebar.toggle_theme.connect(self.toggle_theme)   # 这行压根没写
```

信号定义了、发射端写了，接收端从未连上——所以直接调
`probe.toggle_theme()` 完全正常，而用户点按钮没反应。当时的测试就是
`probe.toggle_theme()`，**绕过了点击路径**，信号断在哪里根本看不见。

```python
before = probe.theme_name
btn.click()                 # 真实的 QPushButton.click()
pump(8)
assert probe.theme_name != before
btn.click(); pump(8)       # 还要能切回来，防「只能切到深色」
assert probe.theme_name == before
```

这条适用于所有靠信号驱动的交互：按钮、菜单、快捷键。

**② 读属性和真的执行一遍，是两件事。**

下拉框点开就崩的那次：

```
NameError: name 'QPainter' is not defined. Did you mean: 'painter'?
```

`QPainter` 来自 `paint()` 里的**局部 import**，清理未用导入时被删了。
import 时不报错，只在 `paint()` 真正执行时炸——而 `paint()` 只在用户点开
下拉框时才被调用，当时的测试只读 delegate 属性，**从没真画过一次**。

```python
cb.showPopup(); pump(10)
cb.view().viewport().grab()      # 这一步才会真正执行 paint()
cb.hidePopup()
```

顺带记住：**局部 import 是另一个命名空间**。清理顶层未用的导入前，先确认
它不是被函数内import 的同名符号替代了。

### 改了功能就更新文档，并跑 `tests/check_docs.py`

**README / CONTRIBUTING 腐化是这个项目最常见的文档问题。** 实际发生过的：
改测速时长（30 MB 固定 → 按时间）、主题按钮位置（侧边栏 → 标题栏），
README 和 CONTRIBUTING 都没跟着改，一路带着过三个版本没人发现。

```bash
python tests/check_docs.py      # 24 项，逐条对着运行时断言
```

它检查的是**具体数字和名称**，不是模糊的描述 —— 测速时长选项、
默认秒数、体积、网络修复 8 项的名称和顺序、风险分档、Python 版本要求
是否与代码一致。

**这个脚本必须真的抓得到东西。** 加了新检查项后，故意把文档改错一次
确认它会红：

```bash
cp README.md /tmp/bak && sed -i 's/默认 10 秒/默认 20 秒/' README.md
python tests/check_docs.py; echo "exit=$?"   # 期望 exit=1
cp /tmp/bak README.md
```

永远绿的检查脚本等于没有。

**注意它不是第四个测试套件。** 三个套件（`run_tests` / `test_qt_ui` /
`test_qt_window`）必须全绿才能提交；这个是写文档时的自查，不进 CI。

### 注释只写技术判断，不记录改动缘由

代码里很容易留下两类不该出现的东西：

**转述式引用**（反例，原样展示）：

    ✗ 「某某说这个图标不好看，指的就是这个」
    ✓ 「描边版的月牙被挖掉一大块后，可见面积小，同样线宽显得比方框弱」

**过程叙事**（反例，原样展示）：

    ✗ 「第一次实跑就是这样翻车的」「写错了很久，一直没人发现」
    ✓ 「Actions 没有真正的三元运算符，要按平台取值就用矩阵变量」

**要改的是叙述方式，不是技术内容。** 下面这条注释的判断完全正确，
只是不该把来龙去脉写成对话：

```python
# ✗ 转述了某个来源的说法「这个图标不好看」，指的就是这个
# ✓ 描边版的月牙被挖掉一大块后，可见面积天然小，同样线宽显得比旁边的方框弱
```

**判断标准**：说的是代码和任务本身，还是在复述一段过程。前者留着，
后者删掉——半年后读代码的人需要知道的是「为什么这样写」，不是「当时
发生了什么」。

### core 层和 UI 层的事件协议必须同步改

这是本项目最容易出问题的地方。core 的探测函数用
`post()` 发事件，UI 页面用 `on_event()` 消费，**两边对不上时页面会
静默失效** —— 不报错、不崩溃，控件数量正常，只是永远不出结果。

历史教训：

- 端口检测页只处理了 `scan_begin` 就 `return`，每扫完一个端口发的
  `{"kind": "port", ...}` 全被丢弃，页面**一个结果都不显示**
- WiFi 扫描发的统计标签和页面声明的**交集为空**，四个统计块永远 `—`
- 测速页 core 发 `speed_progress` / `speed_done`，UI 监听 `progress` /
  `done`，字段名也全不一样

**改 core 的 `post()` 时，同步检查对应页面。** 统计块用
`Page.set_tiles()` **按位置**绑定，不要按标签文本匹配 —— 标签一改，
UI 就静默死掉。

**未识别的统计事件要留下痕迹**（例如打印 `[未处理的统计事件]`），
被吞掉的 payload 和正常的看起来一模一样。

### core 层不能直接依赖 UI

`netdiag/core/` 下的代码必须能脱离 Qt 独立测试。它只通过 `post()` 发
事件、接收一个 `stop_check` 回调，不 import 任何 Qt 东西。

### 取消用 `Cancelled(BaseException)`

探测函数里有十几处 `except Exception` 用来兜网络错误并继续（比如测速
换下一个源）。所以取消异常**必须继承 `BaseException`**，否则会被一并
吞掉 —— 用户点了停止，测速照样把所有源轮着跑完。

### 统计块单位要按 key 判断，不能按值类型

core 的 `min` / `max` 是 `int()` 转出来的，`avg` 是除法结果 `float`。
若按 `isinstance(val, float)` 决定加不加单位，四个块里会有两个没
单位（显示「最低 11」，需自行判断是毫秒还是微秒）。

### Windows 和中文输出

- 入口脚本顶部要有 `sys.stdout.reconfigure(encoding="utf-8",
  errors="replace")`。CI 的 `windows-latest` 控制台是 cp1252，
  `print` 中文会直接 `UnicodeEncodeError`，测试连第一行都打不出来。
- 拉子进程输出用 `subprocess.run(..., encoding="utf-8",
  errors="replace")`，**不要用 `text=True`** —— 它会用父进程的 locale
  去解子进程的 UTF-8 输出，抛出的 `UnicodeDecodeError` 会让
  `r.stdout` 变成 `None`，真正的错误被埋在后面的 `AttributeError` 底下。
- 探测 `ping` / `tracert` / `netsh` / `ipconfig` 的输出时，中英文
  Windows 的措辞都兼容（`time=` / `time<` / `时间=` / `时间<`）。

### 测速源要实测，而且要复测

公开镜像的可用性变化极快，失效形式包括：限流 418、404、403、DNS
解析失败，以及只返回少量文本的假源。

- 源必须用 `Range` 头限量下载。**主判据是用户选的时长**（默认 10 秒），
  不是固定大小——固定 30 MB 在链路上够快就量不准（3 秒内下完，剩下的时间
  浪费在等待上），太慢又会被误判成「带宽不足」。
  `Range` 的上限（`_RANGE_CAP`）只作为服务器侧的护栏，防止有人填 60 秒
  就真去下6 GB 的 ISO
- **必须校验内容是不是 HTML**。镜像站对非浏览器请求会返回 HTML 错误页，
  200 + 几百 KB 文本在测速里表现为「超快」，比源挂掉更坑
- 判据不能用「首字节是不是 `<`」—— ISO 头部可能是 `0x3C`

### 图标和素材

图标是 `tools/make_icon.py` 用数学方式生成的原创设计。**不要引入
第三方图标库、字体文件或现成素材** —— 版权要求是硬性的。程序只引用
系统字体名，不分发字体文件。

## 提交信息

用中文，遵循 Conventional Commits 的类型前缀：

```
feat: 新增 XXX 功能
fix: 修复 XXX
docs: 只改文档
refactor: 不改行为的重构
test: 只改测试
```

## 许可

贡献的代码以 **GPL-3.0-or-later** 发布。如果你借鉴了别人的方案，
**必须在 PR 里注明来源和许可** —— 这也是本项目的要求。

## 提交 PR 之前

- [ ] 三个测试套件全绿
- [ ] 改了 core 的 `post()` 就同步检查了对应 UI 页面
- [ ] 改了按钮行为就**点按钮**测，不要直接调槽函数
- [ ] 改了自绘控件（delegate / paintEvent）就**真画一次**（`grab()`）
- [ ] 改了功能就更新了 README，并跑过 `python tests/check_docs.py`
- [ ] 注释只写技术判断，没有转述式引用和过程叙事
- [ ] 没有引入第三方素材或依赖
- [ ] 引用了别人的方案就在 PR 里注明

## 报告 bug

开 issue 时请附上：

- Windows 版本
- 复现步骤
- 实际结果 vs 预期结果
- 版本号 —— **标题栏软件名后面就有**，或看「关于」页。
  截图时记得把它一起截进来

网络类问题建议附上「网络信息」页和「局域网」页的输出 —— 大部分问题
一看网关和 DNS 就定位了。
