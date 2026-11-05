#!/usr/bin/env bash
# 在改写后的墨采源码树里打一个给 TrollStore 安装的 iOS 测试包。
#
#   python3 brand/apply.py apply --root ~/worktrees/mocai-build
#   ~/worktrees/mocai-build/brand/build-trollstore-ipa.sh [输出目录]
#
# 没有付费的 Apple 开发者账号，就不能正常签名。这里先用上游的 build-app.sh 做不签名的真机构建，
# 再用 ldid 伪签名，把 entitlements 写进 App 和键盘扩展。TrollStore 安装时认这份 entitlements，
# 不校验团队，所以团队 ID 用 mocai.toml 里的占位值，只要 App 和扩展一致即可。
# 要写进去的是：application-identifier、App Group（App 和键盘共用的容器），以及 keychain-access-groups。
# 少了后两项，伪签名的 App 读写钥匙串会报 -34018，登录状态就存不下来。
# 依赖：Xcode、xcodegen、CocoaPods、ldid（brew install xcodegen cocoapods ldid）。
set -euo pipefail
export PATH="/opt/homebrew/opt/rustup/bin:$PATH"
root=$(cd "$(dirname "$0")/.." && pwd)
out=${1:-$root/target/ios}
[ -f "$root/BRAND_APPLIED.json" ] || { echo "$root 还没改写，先跑 brand/apply.py apply" >&2; exit 1; }
command -v ldid >/dev/null || { echo "缺 ldid：brew install ldid" >&2; exit 1; }
cd "$root"

cargo run --quiet -p msime-client-core --example install_resources --locked -- target/resources > target/install-resources.log
bash platforms/ios/build-app.sh "$(tail -1 target/install-resources.log)" device

app="$root/target/ios/derived-device/Build/Products/Release-iphoneos/MSIMEApp.app"
team=$(python3 -c 'import tomllib;print(tomllib.load(open("brand/mocai.toml","rb"))["values"]["apple_team_id"])')
group=$(/usr/libexec/PlistBuddy -c 'Print :com.apple.security.application-groups:0' platforms/ios/App/Resources/MSIMEApp.entitlements)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/Payload"
cp -R "$app" "$work/Payload/"
bundle="$work/Payload/MSIMEApp.app"

executable() { /usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$1/Info.plist"; }
sign() { # $1 bundle 目录
  local id
  id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$1/Info.plist")
  cat > "$work/entitlements.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>application-identifier</key><string>$team.$id</string>
  <key>com.apple.developer.team-identifier</key><string>$team</string>
  <key>com.apple.security.application-groups</key><array><string>$group</string></array>
  <key>keychain-access-groups</key><array><string>$team.$id</string><string>$group</string></array>
</dict>
</plist>
PLIST
  ldid -S"$work/entitlements.plist" "$1/$(executable "$1")"
}

# 由内向外签：先框架和动态库，再扩展，最后主程序。
while IFS= read -r -d '' item; do
  if [ -d "$item" ]; then ldid -S "$item/$(executable "$item")"; else ldid -S "$item"; fi
done < <(find "$bundle" \( -name '*.dylib' -o -name '*.framework' \) -print0)
for appex in "$bundle"/PlugIns/*.appex; do
  [ -d "$appex" ] && sign "$appex"
done
sign "$bundle"

# 每个 Mach-O 都要有签名，App 和扩展要带上 App Group。
while IFS= read -r -d '' file; do
  file "$file" | grep -q Mach-O || continue
  codesign -dv "$file" >/dev/null 2>&1 || { echo "没有签名：${file#"$work/"}" >&2; exit 1; }
done < <(find "$work/Payload" -type f -print0)
for binary in "$bundle/$(executable "$bundle")" "$bundle"/PlugIns/*.appex/MSIMEKeyboardExtension; do
  ldid -e "$binary" | grep -q "$group" || { echo "$binary 缺 App Group" >&2; exit 1; }
done

version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$bundle/Info.plist")
commit=$(python3 -c 'import json;print(json.load(open("BRAND_APPLIED.json"))["upstream_commit"][:10])')
mkdir -p "$out"
ipa="$out/墨采输入法-iOS-$version-$commit-trollstore.ipa"
rm -f "$ipa"
(cd "$work" && /usr/bin/zip -qry "$ipa" Payload)
echo "$ipa"
