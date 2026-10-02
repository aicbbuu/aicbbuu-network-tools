; aicbbuu network tools — Inno Setup 安装脚本
;
; 与 tools/sign-exe.ps1 的关系：签名在 CI 里对 exe 做（自签证书 +
; DigiCert 时间戳），本脚本负责把**已签名**的 exe 装进标准目录、
; 建开始菜单与桌面快捷方式、写卸载信息。
;
; 为什么是 Inno Setup 而不是 NSIS：NSIS 需要单独下载安装器，CI 里
; 还得自己解 zip 找 nsis.exe；Inno Setup 在 windows-latest runner 上
; 可以直接 `choco install innosetup`，一条命令搞定。
;
; 免安装 exe 与安装版**并存**：Release 页同时给两个资产，喜欢
; 绿色便携的用户下 exe，想进开始菜单和「应用和功能」的下安装包。
; 两者内容完全一致，只是安装版多了快捷方式、卸载项和注册表信息。

#define AppName        "aicbbuu network tools"
#define AppNameCn      "aicbbuu 网络工具"
#define AppVersion     "1.0.1"
#define AppPublisher   "aicbbuu"
#define AppExeName     "aicbbuu-network-tools.exe"
#define AppId          "{{6B2E9A1C-4F3D-4E7A-9C15-8D2F6B0A7E31}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
OutputBaseFilename=aicbbuu-network-tools-Setup-{#AppVersion}-win-x64
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayName={#AppNameCn} {#AppVersion}
UninstallDisplayIcon={app}\{#AppExeName}
; **Setup.exe 自己的图标。不写这一行 Inno 就用内置默认图标**
; （文档：If this directive is not specified or is blank, a built-in icon
; supporting the above sizes will be used）——这就是安装包在资源管理器
; 里显示成默认灰色图标的原因。
; 路径相对 .iss 所在目录（packaging\），所以要 ..\ 回到仓库根。
; 官方建议至少含 16/32/48/64/256，tools/make_icon.py 全都打进去了。
SetupIconFile=..\netdiag\assets\icon.ico
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=..\artifacts\installer
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 免安装 exe 本身就是用户级工具，没有理由要管理员权限。装到
; Program Files 反而会让普通用户在 C:\ 根目录建不出快捷方式，
; 而且每次升级都要 UAC。
; 固定装到用户目录并**不**给「装到 Program Files」的选项——两种位置
; 权限模型不同，允许切换会出现「这次装这里、下次装那里，卸载项
; 指向另一边」的情况。
PrivilegesRequired=lowest
; 关键：**不写** PrivilegesRequiredOverridesAllowed。
; 默认值是 dialog，会在安装向导里问「为所有用户安装还是仅为你自己」，
; 选「所有用户」会改装到 Program Files 并弹 UAC——但快捷方式写在
; 当前用户的开始菜单里，卸载项也指向另一个位置，越弄越乱。
; 写成 commandline 更糟：命令行参数能无声地切到全局安装。
; 这个软件是用户级工具，固定只装给自己。
; 自签证书在用户眼里和「未知发布者」一样弹 SmartScreen 警告，
; 这里提前说明，避免用户以为是病毒。
SetupLogging=yes

[Languages]
; **顺序：中文在前。** 中文用户直接进中文向导，不用先弹一个
; 「Select Language」再选一次。
;
; 两行 MessagesFile 必须指向**各自**的语言文件，都指向 Default.isl
; 时向导会全是英文——而这不会导致编译失败，只能靠肉眼看出来。
;
; ChineseSimplified.isl 是**非官方**翻译，不在 choco 的 innosetup
; 包里，release.yml 的「准备中文语言文件」那一步会先把它下载到
; 编译器的 Languages 目录。compiler: 前缀表示从编译器目录找，
; 而不是从 .iss 所在目录找。
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
Name: "autostart"; Description: "开机自动启动"; GroupDescription: "附加任务:"

[Files]
; CI 把已签名 exe 拷到仓库根的 artifacts\publish\win-x64\。
;
; **路径的 ..\ 是必须的。** Inno Setup 6 的相对路径基准是本 .iss
; 文件所在目录（packaging\），不是 CI 的工作目录。写成
; artifacts\... 会被解析成 packaging\artifacts\...，
; 报「Source file does not exist」。
;
; 注意别去找 SourceRoot 这个指令：那是 Inno Setup 7 才有的，
; 6.7.1（CI 上 choco 装的就是这个）会报
; Unrecognized [Setup] section directive "SourceRoot"。
Source: "..\artifacts\publish\win-x64\{#AppExeName}"; \
    DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppNameCn}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppNameCn}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppNameCn}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon
; 开机自启放在「启动」文件夹而不是开始菜单——开始菜单快捷方式
; 已经由上面的 [Icons] 无条件创建，再加一条会在同一个目录里出现
; 两个几乎一样的图标。启动文件夹是自启的正规位置。
Name: "{userstartup}\{#AppNameCn}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: autostart

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即运行 {#AppNameCn}"; \
    Flags: nowait postinstall skipifsilent
