"""
aicbbuu network tools — 网络诊断工具
Copyright (C) 2026 aicbbuu

本程序是自由软件：你可以按自由软件许可证的条款重新分发和/或修改它。
本程序按 GNU 通用公共许可证 v3.0 发布。详见 LICENSE 文件。

SPDX-License-Identifier: GPL-3.0-or-later
"""

# 版本号的**唯一来源**。app.py 若自行定义一份 APP_VERSION，
# 两处会不一致，因此统一从这里取。
__version__ = "1.0.0"
__author__ = "aicbbuu"
__license__ = "GPL-3.0-or-later"
__app_name__ = "aicbbuu network tools"

#: 供 UI 层引用（页面标题、窗口标题、关于页）
APP_TITLE = __app_name__
APP_VERSION = __version__
REPO_URL = "https://github.com/aicbbuu/aicbbuu-network-tools"
