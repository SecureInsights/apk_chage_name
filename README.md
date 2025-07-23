# 📱 APK应用名一键修改工具

> 无需 Android Studio，一条命令即可修改任意 APK 的 **应用名称** 并 **重新签名**，生成可直接安装的全新 APK 文件。

---

## ✨ 特性

- ✅ **零环境依赖**：仅需系统已安装 Java 8+  
- ✅ **傻瓜式交互**：命令行问答式输入，无需手动改 XML  
- ✅ **全链路处理**：解码 → 改名称 → 对齐 → 签名 → 输出  
- ✅ **自动签名**：首次运行自动生成 keystore，后续复用  
- ✅ **超时保护**：所有交互步骤 5 秒无响应自动继续，CI 友好  
- ✅ **跨平台**：Windows / macOS / Linux 通用  

---

## 🚀 快速开始

### 1️⃣ 准备工具

将下列文件放在同一目录：

| 文件 | 说明 | 下载地址 |
|---|---|---|
| `apktool.jar` | 反编译 / 回编译 | [Apktool 官方](https://ibotpeaches.github.io/Apktool/) |
| `apksigner.jar` | Google 官方签名工具 | 随 Android SDK build-tools 附带，或自行搜索 |
| `zipalign` / `zipalign.exe` | APK 字节对齐 | 同上，位于 build-tools |
| 本脚本 | `rename_apk.py` | 复制本文代码即可 |

### 2️⃣ 运行脚本

```bash
python3 rename_apk.py
```

按提示输入：

```
请输入新的应用名称（例如：Toollist）: MyAwesomeApp
请输入源APK文件路径 (直接回车使用默认值'app-release.apk'): app-release.apk
请输入输出APK文件路径 (直接回车使用默认值'MyAwesomeApp.apk'):
```

完成后将在当前目录获得 `MyAwesomeApp.apk`，可直接安装。

---

## 🛠️ 高级用法

### 自定义签名参数

编辑脚本顶部常量即可：

```python
KEYSTORE_PATH = Path("my.keystore")
KEY_ALIAS     = "release"
KEY_PASSWORD  = "S3cret"
```

### 静默模式 / CI 集成

所有交互均支持环境变量或默认值，结合 `timeout` 可实现无人值守：

```bash
echo -e "MyApp\napp.apk\n" | python3 rename_apk.py
```

---

## 📁 目录结构示例

```
workspace/
├── rename_apk.py
├── apktool.jar
├── apksigner.jar
├── zipalign
├── my-release-key.keystore   (第一次运行后自动生成)
├── app-release.apk           (输入)
└── MyApp.apk                 (输出)
```

---

## 🧹 临时文件

脚本运行时会在 `./apk_workdir` 创建临时文件，成功后可选择自动清理。

---

## 🤝 贡献 & 反馈

遇到问题或有新功能建议，欢迎提 [Issue](https://github.com/yourname/apk-rename-tool/issues) 或 [Pull Request](https://github.com/yourname/apk-rename-tool/pulls)。
```
