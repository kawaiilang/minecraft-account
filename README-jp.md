# minecraft-auth

[English](README.md) | 日本語

Linux の Minecraft Java Edition 用の認証ヘルパーです。Microsoft のデバイスコードでログインし、アカウントを GNOME Keyring に保存します。ランチャーが用意した Java の起動コマンドに、封印した無名ファイル記述子を介して認証情報を渡します。

## 手元で使う

ログインには、空でないパスワードで保護した永続的な既定の GNOME キーリングとデスクトップセッションが必要です。x86_64 または aarch64 Linux では、このリポジトリ内で次のコマンドを実行すると、Nix が Python と GUI の依存関係を用意します。

```sh
nix run . -- --help
nix run . -- login
```

Python でインストールする場合は 3.10 以降を使います。GUI を使う場合は Zenity と xdg-open も用意してください。

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/minecraft-auth --help
.venv/bin/minecraft-auth login
.venv/bin/minecraft-auth status
```

Linux ユーザーごとに1アカウントを登録できます。`minecraft-auth --gui` で管理画面を開き、`minecraft-auth logout` でローカルの登録を削除できます。ランチャーからはゲームディレクトリ内で `minecraft-auth launch -- /absolute/path/to/java ...` に通常の Java 引数とゲーム引数を渡します。ユーザー名、UUID、アクセストークンはヘルパーが設定します。

## テスト

`nix flake check` でパッケージのビルド、テスト、Lint・型チェック・ワークフロー監査を一括実行できます。Nix ファイルの整形には `nix fmt`、同じツール構成の開発シェルには `nix develop` を使えます。他の flake からは `lib.mkAuth { inherit pkgs; }` で利用側のパッケージセットを渡せます。テスト用 Java を指定する引数 `checkJdk` の既定値は `pkgs.jdk25` です。ゲーム起動時の Java はランチャーが指定します。

**CI** ワークフローでは、静的解析（`ruff`・`pyright`・`nixfmt`・`statix`・`deadnix`・`actionlint`・`zizmor`）、Nix パッケージ検証に加え、Ubuntu 上で架空のアカウントを使い、認証応答の処理、実際の Java プロセスへの引き渡し、Xvfb 上の Zenity、専用 D-Bus セッション内の一時キーリングを検証します。[CI ワークフローを見る](.github/workflows/ci.yml)

## 派生元

[Nixcraft の client-auth.py（fd933a2）](https://github.com/loystonpais/nixcraft/blob/fd933a2806fbc8c1627018a80489964d618b0c2c/nix/client-auth/client-auth.py)にキーリング保存と Java 起動の変更を取り込んで独立させました。Microsoft・Xbox・Minecraft の認証仕様が変わった際は上流と照合してください。[GPL-3.0-only ライセンス](LICENSE)
