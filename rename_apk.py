#!/usr/bin/env python3

import os
import re
import shutil
from pathlib import Path
from subprocess import run, CalledProcessError, PIPE, STDOUT
import time
import threading

# ========== 用户配置 ==========
NEW_NAME = input("请输入新的应用名称（例如：Toollist）: ").strip()
if not NEW_NAME:
    print("❌ 应用名称不能为空")
    exit(1)

apk_in = input("请输入源APK文件路径 (直接回车使用默认值'app-release.apk'): ").strip()
APK_IN = Path(apk_in if apk_in else "app-release.apk")

apk_out = input(f"请输入输出APK文件路径 (直接回车使用默认值'{NEW_NAME}.apk'): ").strip()
APK_OUT = Path(apk_out if apk_out else f"{NEW_NAME}.apk")
APKTOOL_JAR = Path("apktool.jar")
ZIPALIGN_PATH = Path("zipalign")

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
# 修改AndroidManifest.xml、包名及相关文件（增强版）
# ------------------------------------------------------------------
def patch_manifest_and_optimize(src: Path, dst: Path, new_name: str) -> None:
    """解码、修改应用名称、包名及所有关联属性（含android:authorities）"""
    decoded_dir = WORK_DIR / "decoded"
    
    # 强制清理旧目录
    if decoded_dir.exists():
        try:
            shutil.rmtree(decoded_dir)
            print(f"🧹 清理旧解码目录")
        except Exception as e:
            print(f"⚠️ 清理旧目录失败: {str(e)}")
            raise

    print("📦 解码APK文件（含dex反编译）...")
    try:
        decode_args = [
            str(JAVA_PATH), "-jar", str(APKTOOL_JAR), 
            "d", "-v", "-o", str(decoded_dir), str(src)
        ]
        result = run(decode_args, check=True, capture_output=True, text=True)
    except CalledProcessError as e:
        print(f"❌ APK解码失败: {e.stderr}")
        print("💡 可能原因：APK被加固/加密，请先脱壳；或apktool版本不兼容")
        raise
    except Exception as e:
        print(f"❌ 解码过程发生错误: {str(e)}")
        raise
    
    # 查找AndroidManifest.xml
    manifest_path = decoded_dir / "AndroidManifest.xml"
    if not manifest_path.exists():
        alt_manifest = decoded_dir / "original" / "AndroidManifest.xml"
        if alt_manifest.exists():
            print(f"⚠️ 使用替代清单文件: {alt_manifest}")
            manifest_path = alt_manifest
        else:
            raise FileNotFoundError(f"❌ 未找到AndroidManifest.xml: {manifest_path}")
    
    # 读取Manifest内容
    try:
        with open(manifest_path, "r", encoding="utf-8", errors="ignore") as f:
            manifest_data = f.read()
    except Exception as e:
        print(f"❌ 读取清单文件失败: {str(e)}")
        raise
    
    # 修改应用名称
    print(f"✏️ 修改应用名称为: {new_name}")
    app_pattern = re.compile(r'(<application\s[^>]*?android:label=)"([^"]+)"', re.IGNORECASE | re.DOTALL)
    if app_pattern.search(manifest_data):
        manifest_data = app_pattern.sub(f'\\1"{new_name}"', manifest_data)
    else:
        app_tag_pattern = re.compile(r'(<application\s[^>]*?)>', re.IGNORECASE)
        if app_tag_pattern.search(manifest_data):
            manifest_data = app_tag_pattern.sub(f'\\1 android:label="{new_name}">', manifest_data)
        else:
            print("⚠️ 未找到application标签，无法修改应用名称")


    # -------------------------- 包名识别与修改（增强版） --------------------------
    # 1. 提取原包名
    package_pattern = re.compile(r'package="([^"]+)"', re.IGNORECASE)
    match = package_pattern.search(manifest_data)
    if not match:
        raise ValueError("❌ 未在AndroidManifest.xml中找到package属性，无法识别原包名")
    original_package = match.group(1)
    print(f"🔍 识别到原包名: {original_package}")

    # 2. 生成新包名
    new_name_lower = new_name.lower()
    original_parts = original_package.split('.')
    new_package = '.'.join(original_parts[:-1] + [new_name_lower]) if len(original_parts)>=2 else new_name_lower
    print(f"✏️ 生成新包名: {new_package}")

    # 3. 修改Manifest中的package属性
    manifest_data = package_pattern.sub(f'package="{new_package}"', manifest_data)
    print(f"✏️ 已更新Manifest中的package属性为: {new_package}")


    # -------------------------- 新增：处理android:authorities --------------------------
    # 匹配格式：android:authorities="com.flet.hello_flet.androidx-startup"
    # 或带多个值：android:authorities="com.flet.hello_flet.provider,com.flet.hello_flet.fileprovider"
    authorities_pattern = re.compile(
        rf'(android:authorities=")[^"]*?{re.escape(original_package)}[^"]*?"', 
        re.IGNORECASE
    )
    
    # 查找所有匹配项
    authorities_matches = authorities_pattern.findall(manifest_data)
    if authorities_matches:
        print(f"🔍 发现{len(authorities_matches)}处android:authorities引用原包名")
        
        # 替换逻辑：将原包名替换为新包名
        def replace_authorities(match):
            original_authorities = match.group(0)
            # 替换所有出现的原包名
            new_authorities = original_authorities.replace(original_package, new_package)
            return new_authorities
        
        # 执行替换
        manifest_data = authorities_pattern.sub(replace_authorities, manifest_data)
        print(f"✏️ 已更新所有android:authorities属性，替换原包名为新包名")
    else:
        print("ℹ️ 未发现android:authorities引用原包名，无需修改")


    # -------------------------- 处理其他包名引用 --------------------------
    # 1. 修改组件绝对路径（如android:name="com.flet.hello_flet.MainActivity"）
    original_package_escaped = re.escape(original_package)
    abs_ref_pattern = re.compile(
        rf'(android:name="){original_package_escaped}(\.[^"]+")', 
        re.IGNORECASE
    )
    manifest_data = abs_ref_pattern.sub(rf'\1{new_package}\2', manifest_data)
    print(f"✏️ 已更新Manifest中组件的包名引用")

    # 2. 修改权限声明（如<permission android:name="com.flet.hello_flet.permission.XXX"）
    permission_pattern = re.compile(
        rf'(android:name="){original_package_escaped}(\.[^"]+")', 
        re.IGNORECASE
    )
    manifest_data = permission_pattern.sub(rf'\1{new_package}\2', manifest_data)
    print(f"✏️ 已更新Manifest中权限声明的包名引用")


    # -------------------------- 处理Smali文件 --------------------------
    original_path = original_package.replace('.', '/')
    new_path = new_package.replace('.', '/')
    print(f"🔍 原Smali路径: {original_path}")
    print(f"✏️ 新Smali路径: {new_path}")

    # 查找smali目录
    smali_dirs = [d for d in decoded_dir.iterdir() if d.is_dir() and d.name.startswith('smali')]
    if not smali_dirs:
        deep_smali = list(decoded_dir.rglob('smali*/'))
        if deep_smali:
            smali_dirs = deep_smali
        else:
            raise FileNotFoundError("❌ 未找到任何smali目录！请确保APK已脱壳并正确反编译")
    
    # 处理每个smali目录
    for smali_root in smali_dirs:
        # 移动原包名目录到新目录
        original_smali_dir = smali_root / original_path
        if original_smali_dir.exists():
            new_smali_dir = smali_root / new_path
            new_smali_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(original_smali_dir), str(new_smali_dir))
                print(f"📁 已移动Smali目录: {original_smali_dir} → {new_smali_dir}")
            except Exception as e:
                print(f"⚠️ 移动Smali目录失败: {str(e)}，将尝试手动替换内容")

        # 批量替换Smali文件中的包名引用
        print(f"🔄 替换{smali_root}目录下所有Smali文件的包名...")
        for smali_file in smali_root.rglob('*.smali'):
            try:
                with open(smali_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                # 替换类定义和引用
                updated_content = content.replace(original_path, new_path)
                
                if updated_content != content:
                    with open(smali_file, 'w', encoding='utf-8', errors='ignore') as f:
                        f.write(updated_content)
            except Exception as e:
                print(f"⚠️ 处理Smali文件 {smali_file} 时出错: {str(e)}")


    # 写入修改后的Manifest
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(manifest_data)
    except Exception as e:
        print(f"❌ 写入清单文件失败: {str(e)}")
        raise
    
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
        print("💡 可能原因：Smali文件修改有误，存在语法错误")
        raise
    except Exception as e:
        print(f"❌ 打包过程发生错误: {str(e)}")
        raise


# ------------------------------------------------------------------
# 对齐APK
# ------------------------------------------------------------------
def zipalign_apk(input_apk: Path, output_apk: Path) -> None:
    print("📏 对齐APK文件...")
    try:
        if output_apk.exists():
            output_apk.unlink()
        run([str(ZIPALIGN), "-v", "4", str(input_apk), str(output_apk)], check=True, capture_output=True, text=True)
        print("✅ APK对齐完成")
    except CalledProcessError as e:
        print(f"❌ APK对齐失败: {e.stderr}")
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
# 签名APK文件
# ------------------------------------------------------------------
def sign_apk(unsigned_apk: Path, signed_apk: Path) -> None:
    print("🔐 签名APK文件...")
    if signed_apk.exists():
        try:
            signed_apk.unlink()
        except Exception as e:
            print(f"⚠️ 无法删除旧签名文件: {str(e)}")
    
    try:
        run([
            str(JAVA_PATH), "-jar", str(APKSIGNER_JAR),
            "sign", "--v1-signing-enabled", "true", "--v2-signing-enabled", "true",
            "--ks", str(KEYSTORE_PATH), "--ks-key-alias", KEY_ALIAS,
            "--ks-pass", f"pass:{KEYSTORE_PASSWORD}", "--key-pass", f"pass:{KEY_PASSWORD}",
            "--out", str(signed_apk), str(unsigned_apk)
        ], check=True, capture_output=True, text=True)
    except CalledProcessError as e:
        print(f"❌ 签名过程失败: {e.stderr}")
        raise
    except Exception as e:
        print(f"❌ 签名过程发生错误: {str(e)}")
        raise
    
    # 验证签名
    print("✅ 验证签名...")
    try:
        result = run([str(JAVA_PATH), "-jar", str(APKSIGNER_JAR), "verify", "--verbose", str(signed_apk)], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"❌ 签名验证失败: {result.stderr}")
        print("✅ 签名验证成功")
    except Exception as e:
        print(f"❌ 签名验证失败: {str(e)}")
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
                def get_input(): response['value'] = input("是否生成新的签名文件? (y/n) ").lower()
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
        
        # 步骤1: 修改应用名称、包名及所有关联属性
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
        if WORK_DIR.exists():
            try:
                response = {'value': None}
                def get_input(): response['value'] = input("是否清理临时文件? (y/n) ").lower()
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
