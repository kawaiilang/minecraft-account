# minecraft-account

[English](README.md)

Linux の Minecraft Java Edition 用の認証ヘルパーです。Microsoft のデバイスコードでログインし、アカウントを GNOME Keyring に保存します。ランチャーが用意した Java の起動コマンドに、封印した無名ファイル記述子を介して認証情報を渡します。

## 手元で使う

Python 3.10 以降と、空でないパスワードで保護した永続的な既定の GNOME キーリングが必要です。GUI を使う場合は Zenity と xdg-open も用意してください。

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/minecraft-account --help
.venv/bin/minecraft-account login
.venv/bin/minecraft-account status
```

Linux ユーザーごとに1アカウントを登録できます。`minecraft-account --gui` で管理画面を開き、`minecraft-account logout` でローカルの登録を削除できます。ランチャーからはゲームディレクトリ内で `minecraft-account launch -- /absolute/path/to/java ...` に通常の Java 引数とゲーム引数を渡します。ユーザー名、UUID、アクセストークンはヘルパーが設定します。

## テスト

**Account tests** は Ubuntu 上で架空のアカウントを使い、認証応答の処理、実際の Java プロセスへの引き渡し、Xvfb 上の Zenity、専用 D-Bus セッション内の一時キーリングを検証します。[テストの実行手順](.github/workflows/test.yml)

## 派生元

[Nixcraft の client-auth.py（fd933a2）](https://github.com/loystonpais/nixcraft/blob/fd933a2806fbc8c1627018a80489964d618b0c2c/nix/client-auth/client-auth.py)にキーリング保存と Java 起動の変更を取り込んで独立させました。Microsoft・Xbox・Minecraft の認証仕様が変わった際は上流と照合してください。[GPL-3.0-only ライセンス](LICENSE)
