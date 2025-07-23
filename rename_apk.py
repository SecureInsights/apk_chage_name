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
        # 保持原始命令参数不变
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
        # 尝试从替代位置查找
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
        # 如果找不到application标签内的label，尝试添加一个
        app_tag_pattern = re.compile(r'(<application\s[^>]*?)>', re.IGNORECASE)
        if app_tag_pattern.search(manifest_data):
            manifest_data = app_tag_pattern.sub(f'\\1 android:label="{new_name}">', manifest_data)
        else:
            print("⚠️ 未找到application标签，无法修改应用名称")
    
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(manifest_data)
    except Exception as e:
        print(f"❌ 写入清单文件失败: {str(e)}")
        raise
    
    # 重新打包APK
    print("📦 重新打包APK...")
    try:
        # 保持原始命令参数不变
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
        # 删除可能存在的输出文件
        if output_apk.exists():
            output_apk.unlink()
            
        # 保持原始命令参数不变
        run([
            str(ZIPALIGN), "-v", "4",  # 4字节对齐，Android要求的标准
            str(input_apk),
            str(output_apk)
        ], check=True, capture_output=True, text=True)
        print("✅ APK对齐完成")
    except CalledProcessError as e:
        print(f"❌ APK对齐失败: {e.stderr}")
        # 尝试复制原始文件继续处理，作为降级方案
        try:
            shutil.copy2(str(input_apk), str(output_apk))
            print("⚠️ 继续使用未对齐的APK，可能导致安装失败")
        except Exception as ce:
            print(f"❌ 复制文件失败: {str(ce)}")
            raise
    except FileNotFoundError:
        print("❌ 未找到zipalign工具，无法对齐APK")
        # 复制原始文件继续处理
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
        try:
            signed_apk.unlink()
        except Exception as e:
            print(f"⚠️ 无法删除旧签名文件: {str(e)}")
    
    try:
        # 保持原始命令参数不变
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
    
    # 详细验证签名
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
        # 检查输入APK是否存在
        if not APK_IN.exists():
            print(f"❌ 输入APK文件不存在: {APK_IN}")
            return
        
        # 检查并生成签名文件
        if not KEYSTORE_PATH.exists():
            print(f"⚠️ 未找到签名文件: {KEYSTORE_PATH}")
            try:
                
                # 用于存储用户输入
                response = {'value': None}
                
                # 获取用户输入的函数
                def get_input():
                    response['value'] = input("是否生成新的签名文件? (y/n) ").lower()
                
                # 创建输入线程
                input_thread = threading.Thread(target=get_input)
                input_thread.daemon = True
                input_thread.start()
                
                # 等待5秒或用户输入
                input_thread.join(timeout=5)
                
                # 如果5秒内没有输入，自动设为'y'
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
        
        # 确保工作目录存在
        try:
            WORK_DIR.mkdir(exist_ok=True)
        except Exception as e:
            print(f"❌ 无法创建工作目录: {str(e)}")
            return
        
        # 步骤1: 修改AndroidManifest.xml
        patched_apk = WORK_DIR / "patched_unsigned.apk"
        try:
            patch_manifest_and_optimize(APK_IN, patched_apk, NEW_NAME)
            if not patched_apk.exists():
                raise FileNotFoundError("修改后的APK文件未生成")
        except Exception as e:
            print(f"🔚 修改APK失败: {str(e)}")
            return
        
        # 步骤2: 对齐APK
        aligned_apk = WORK_DIR / "patched_aligned.apk"
        try:
            zipalign_apk(patched_apk, aligned_apk)
            if not aligned_apk.exists():
                raise FileNotFoundError("对齐后的APK文件未生成")
        except Exception as e:
            print(f"🔚 APK对齐失败: {str(e)}")
            return
        
        # 步骤3: 签名APK
        signed_apk = WORK_DIR / "patched_signed.apk"
        try:
            sign_apk(aligned_apk, signed_apk)
            if not signed_apk.exists():
                raise FileNotFoundError("签名后的APK文件未生成")
        except Exception as e:
            print(f"🔚 APK签名失败: {str(e)}")
            return
        
        # 步骤4: 拷贝最终结果
        try:
            if APK_OUT.exists():
                APK_OUT.unlink()
            shutil.move(str(signed_apk), APK_OUT)
            print(f"🎉 操作完成！生成文件: {APK_OUT.resolve()}")
            
        except Exception as e:
            print(f"❌ 无法复制最终文件: {str(e)}")
            print(f"💡 可手动获取签名后的文件: {signed_apk}")
    
    except KeyboardInterrupt:
        print("\n🔚 用户中断操作")
    except Exception as e:
        print(f"❌ 发生错误: {str(e)}")
    finally:
        # 清理工作目录
        if WORK_DIR.exists():
            try:
                # 用于存储用户输入
                response = {'value': None}
                
                # 获取用户输入的函数
                def get_input():
                    response['value'] = input("是否清理临时文件? (y/n) ").lower()
                
                # 创建输入线程
                input_thread = threading.Thread(target=get_input)
                input_thread.daemon = True
                input_thread.start()
                
                # 等待5秒或用户输入
                input_thread.join(timeout=5)
                
                # 如果5秒内没有输入，自动设为'y'
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
