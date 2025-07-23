#!/usr/bin/env python3

import os
import re
import shutil
from pathlib import Path
from subprocess import run, CalledProcessError, PIPE, STDOUT
import time
import threading

# ========== 用户配置 ==========
# 获取用户输入的新应用名称
NEW_NAME = input("请输入新的应用名称（例如：Toollist）: ").strip()
if not NEW_NAME:
    print("❌ 应用名称不能为空")
    exit(1)

# 新增：获取用户输入的新包名（留空保持原包名）
NEW_PKG = input("请输入新的包名（留空保持原包名）: ").strip()

# 获取用户输入的源APK文件路径
apk_in = input("请输入源APK文件路径 (直接回车使用默认值'app-release.apk'): ").strip()
APK_IN = Path(apk_in if apk_in else "app-release.apk")

# 获取用户输入的输出APK文件路径
apk_out = input(f"请输入输出APK文件路径 (直接回车使用默认值'{NEW_NAME}.apk'): ").strip()
APK_OUT = Path(apk_out if apk_out else f"{NEW_NAME}.apk")
APKTOOL_JAR = Path("apktool.jar")
ZIPALIGN_PATH = Path("zipalign")  # zipalign工具路径

# 签名相关配置
KEYSTORE_PATH = Path("my-release-key.keystore")
KEY_ALIAS = "myalias"
KEY_PASSWORD = "android"
KEYSTORE_PASSWORD = "android"
APKSIGNER_JAR = Path("apksigner.jar")

# 工作目录
WORK_DIR = Path("./apk_workdir")

JAVA_PATH = "java"
ZIPALIGN = "zipalign"

# ------------------------------------------------------------------
# 生成签名文件
# ------------------------------------------------------------------
def generate_keystore():
    try:
        print(f"🔑 生成签名文件: {KEYSTORE_PATH}")
        run([
            "keytool", "-genkey", "-v",
            "-keystore", str(KEYSTORE_PATH),
            "-alias", KEY_ALIAS,
            "-keyalg", "RSA",
            "-keysize", "2048",
            "-validity", "10000",
            "-storepass", KEYSTORE_PASSWORD,
            "-keypass", KEY_PASSWORD,
            "-dname", "CN=Unknown, OU=Unknown, O=Unknown, L=Unknown, ST=Unknown, C=Unknown"
        ], check=True, stdout=PIPE, stderr=PIPE)
        return True
    except CalledProcessError as e:
        print(f"❌ 生成签名文件失败: 命令执行错误")
        print(f"💡 错误详情: {e.stderr.decode('utf-8', errors='ignore')}")
        return False
    except FileNotFoundError:
        print("❌ 未找到keytool工具，请确保JDK已正确安装并配置环境变量")
        return False
    except Exception as e:
        print(f"❌ 生成签名时发生意外错误: {str(e)}")
        return False

# ------------------------------------------------------------------
# 修改AndroidManifest.xml和优化APK结构
# ------------------------------------------------------------------
def patch_manifest_and_optimize(src: Path, dst: Path, new_name: str) -> None:
    """解码、修改、优化并重新编码APK"""
    decoded_dir = WORK_DIR / "decoded"
    
    # 确保解码目录干净
    if decoded_dir.exists():
        try:
            shutil.rmtree(decoded_dir)
            print(f"🧹 清理旧解码目录")
        except Exception as e:
            print(f"⚠️ 清理旧目录失败: {str(e)}")
            raise

    print("📦 解码APK文件...")
    try:
        decode_args = [
            str(JAVA_PATH), "-jar", str(APKTOOL_JAR), 
            "d", "-v", "-o", str(decoded_dir), str(src),
            "--no-src"
        ]
        result = run(decode_args, check=True, capture_output=True, text=True)
    except CalledProcessError as e:
        print(f"❌ APK解码失败: {e.stderr}")
        raise
    except FileNotFoundError as e:
        print(f"❌ 未找到必要文件: {str(e)}")
        raise
    except Exception as e:
        print(f"❌ 解码过程发生错误: {str(e)}")
        raise
    
    # 检查并修改AndroidManifest.xml
    manifest_path = decoded_dir / "AndroidManifest.xml"
    if not manifest_path.exists():
        alt_manifest = decoded_dir / "original" / "AndroidManifest.xml"
        if alt_manifest.exists():
            print(f"⚠️ 使用替代清单文件: {alt_manifest}")
            manifest_path = alt_manifest
        else:
            raise FileNotFoundError(f"❌ 未找到AndroidManifest.xml: {manifest_path}")
    
    print(f"✏️ 修改应用名称为: {new_name}")
    try:
        with open(manifest_path, "r", encoding="utf-8", errors="ignore") as f:
            manifest_data = f.read()
    except Exception as e:
        print(f"❌ 读取清单文件失败: {str(e)}")
        raise
    
    # 更精确地匹配应用标签
    app_pattern = re.compile(r'(<application\s[^>]*?android:label=)"([^"]+)"', re.IGNORECASE | re.DOTALL)
    if app_pattern.search(manifest_data):
        manifest_data = app_pattern.sub(f'\\1"{new_name}"', manifest_data)
    else:
        app_tag_pattern = re.compile(r'(<application\s[^>]*?)>', re.IGNORECASE)
        if app_tag_pattern.search(manifest_data):
            manifest_data = app_tag_pattern.sub(f'\\1 android:label="{new_name}">', manifest_data)
        else:
            print("⚠️ 未找到application标签，无法修改应用名称")

    # 新增：修改包名（若提供）
    old_pkg = None
    if NEW_PKG:
        old_pkg_match = re.search(r'package="([^"]+)"', manifest_data, re.IGNORECASE)
        if old_pkg_match:
            old_pkg = old_pkg_match.group(1)
            manifest_data = re.sub(r'package="[^"]+"', f'package="{NEW_PKG}"', manifest_data, flags=re.IGNORECASE)
            # 简单替换 authorities 等全限定前缀
            manifest_data = manifest_data.replace(f'"{old_pkg}.', f'"{NEW_PKG}.')
    
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(manifest_data)
    except Exception as e:
        print(f"❌ 写入清单文件失败: {str(e)}")
        raise
    
    # 新增：同步 smali 目录及常量（仅简单场景）
    if NEW_PKG and old_pkg:
        old_java = old_pkg.replace('.', '/')
        new_java = NEW_PKG.replace('.', '/')
        # 1) 改 smali 目录
        for smali_root in decoded_dir.glob('smali*'):
            old_path = smali_root / old_java
            new_path = smali_root / new_java
            if old_path.exists() and not new_path.exists():
                new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_path), str(new_path))
        # 2) 改 smali 文件中的常量
        for smali_file in decoded_dir.rglob("*.smali"):
            txt = smali_file.read_text(encoding="utf-8", errors="ignore")
            txt = txt.replace(f"L{old_java}/", f"L{new_java}/")
            smali_file.write_text(txt, encoding="utf-8")
    
    # 重新打包APK
    print("📦 重新打包APK...")
    try:
        build_args = [
            str(JAVA_PATH), "-jar", str(APKTOOL_JAR), 
            "b", "-v", "-f", "-o", str(dst), str(decoded_dir)
        ]
        result = run(build_args, check=True, capture_output=True, text=True)
    except CalledProcessError as e:
        print(f"❌ APK打包失败: {e.stderr}")
        raise
    except Exception as e:
        print(f"❌ 打包过程发生错误: {str(e)}")
        raise

# ------------------------------------------------------------------
# 对齐APK（解决-124错误的关键步骤）
# ------------------------------------------------------------------
def zipalign_apk(input_apk: Path, output_apk: Path) -> None:
    """使用zipalign工具优化APK，提高兼容性"""
    print("📏 对齐APK文件...")
    try:
        if output_apk.exists():
            output_apk.unlink()
        run([
            str(ZIPALIGN), "-v", "4",
            str(input_apk),
            str(output_apk)
        ], check=True, capture_output=True, text=True)
        print("✅ APK对齐完成")
    except CalledProcessError as e:
        print(f"❌ APK对齐失败: {e.stderr}")
        try:
            shutil.copy2(str(input_apk), str(output_apk))
            print("⚠️ 继续使用未对齐的APK，可能导致安装失败")
        except Exception as ce:
            print(f"❌ 复制文件失败: {str(ce)}")
            raise
    except FileNotFoundError:
        print("❌ 未找到zipalign工具，无法对齐APK")
        try:
            shutil.copy2(str(input_apk), str(output_apk))
            print("⚠️ 继续使用未对齐的APK，可能导致安装失败")
        except Exception as ce:
            print(f"❌ 复制文件失败: {str(ce)}")
            raise
    except Exception as e:
        print(f"❌ 对齐过程发生错误: {str(e)}")
        raise

# ------------------------------------------------------------------
# 签名APK文件（增强版）
# ------------------------------------------------------------------
def sign_apk(unsigned_apk: Path, signed_apk: Path) -> None:
    """增强版APK签名，解决签名兼容性问题"""
    print("🔐 签名APK文件...")
    
    if signed_apk.exists():
        signed_apk.unlink()
    
    try:
        run([
            str(JAVA_PATH), "-jar", str(APKSIGNER_JAR),
            "sign",
            "--v1-signing-enabled", "true",
            "--v2-signing-enabled", "true",
            "--v3-signing-enabled", "false",
            "--min-sdk-version", "16",
            "--max-sdk-version", "33",
            "--ks", str(KEYSTORE_PATH),
            "--ks-key-alias", KEY_ALIAS,
            "--ks-pass", f"pass:{KEYSTORE_PASSWORD}",
            "--key-pass", f"pass:{KEY_PASSWORD}",
            "--out", str(signed_apk),
            str(unsigned_apk)
        ], check=True, capture_output=True, text=True)
        
    except CalledProcessError as e:
        print(f"❌ 签名过程失败: {e.stderr}")
        raise
    except FileNotFoundError as e:
        print(f"❌ 未找到必要文件: {str(e)}")
        raise
    except Exception as e:
        print(f"❌ 签名过程发生错误: {str(e)}")
        raise
    
    # 验证签名
    print("✅ 验证签名...")
    try:
        result = run([
            str(JAVA_PATH), "-jar", str(APKSIGNER_JAR),
            "verify", "--verbose", "--print-certs", str(signed_apk)
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            raise RuntimeError(f"❌ 签名验证失败: {result.stderr}")
        print("✅ 签名验证成功")
        
    except CalledProcessError as e:
        print(f"❌ 签名验证失败: {e.stderr}")
        raise
    except Exception as e:
        print(f"❌ 签名验证过程发生错误: {str(e)}")
        raise

# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def main():
    try:
        if not APK_IN.exists():
            print(f"❌ 输入APK文件不存在: {APK_IN}")
            return
        
        if not KEYSTORE_PATH.exists():
            print(f"⚠️ 未找到签名文件: {KEYSTORE_PATH}")
            try:
                response = {'value': None}
                def get_input():
                    response['value'] = input("是否生成新的签名文件? (y/n) ").lower()
                input_thread = threading.Thread(target=get_input)
                input_thread.daemon = True
                input_thread.start()
                input_thread.join(timeout=5)
                if response['value'] is None:
                    print("\n⏰ 等待超时，自动继续...")
                    response['value'] = 'y'
                if response['value'] != 'y':
                    print("🔚 用户取消操作")
                    return
                if not generate_keystore():
                    print("🔚 生成签名文件失败，退出流程")
                    return
            except KeyboardInterrupt:
                print("\n🔚 用户取消操作")
                return
        
        WORK_DIR.mkdir(exist_ok=True)
        
        patched_apk = WORK_DIR / "patched_unsigned.apk"
        patch_manifest_and_optimize(APK_IN, patched_apk, NEW_NAME)
        if not patched_apk.exists():
            raise FileNotFoundError("修改后的APK文件未生成")
        
        aligned_apk = WORK_DIR / "patched_aligned.apk"
        zipalign_apk(patched_apk, aligned_apk)
        if not aligned_apk.exists():
            raise FileNotFoundError("对齐后的APK文件未生成")
        
        signed_apk = WORK_DIR / "patched_signed.apk"
        sign_apk(aligned_apk, signed_apk)
        if not signed_apk.exists():
            raise FileNotFoundError("签名后的APK文件未生成")
        
        if APK_OUT.exists():
            APK_OUT.unlink()
        shutil.move(str(signed_apk), APK_OUT)
        print(f"🎉 操作完成！生成文件: {APK_OUT.resolve()}")
        
    except KeyboardInterrupt:
        print("\n🔚 用户中断操作")
    except Exception as e:
        print(f"❌ 发生错误: {str(e)}")
    finally:
        if WORK_DIR.exists():
            try:
                response = {'value': None}
                def get_input():
                    response['value'] = input("是否清理临时文件? (y/n) ").lower()
                input_thread = threading.Thread(target=get_input)
                input_thread.daemon = True
                input_thread.start()
                input_thread.join(timeout=5)
                if response['value'] is None:
                    print("\n⏰ 等待超时，自动继续...")
                    response['value'] = 'y'
                if response['value'] == 'y':
                    shutil.rmtree(WORK_DIR, ignore_errors=True)
                    print("🧹 临时文件已清理")
                else:
                    print(f"📁 临时文件保留在: {WORK_DIR}")
            except KeyboardInterrupt:
                print("\n⚠️ 取消清理临时文件")
            except Exception as e:
                print(f"⚠️ 清理临时文件失败: {str(e)}")

if __name__ == "__main__":
    main()
