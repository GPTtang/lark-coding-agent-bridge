"""Match the bot's groups by name to groups.json and write dir_map.json."""
from config import DIR_MAP_PATH, load_groups, save_json
from feishu_api import list_bot_chats


def main() -> None:
    chats = {c["name"]: c for c in list_bot_chats()}
    dir_map, missing = {}, []
    for name, (directory, agent) in load_groups().items():
        chat = chats.get(name)
        if not chat:
            missing.append(name)
            continue
        dir_map[chat["chat_id"]] = {"name": name, "dir": directory, "agent": agent,
                                    "owner_open_id": chat.get("owner_id", "")}
        print(f"✓ {name:<12} -> {directory} ({agent})")
    save_json(DIR_MAP_PATH, dir_map)
    if missing:
        print(f"\n✗ 机器人不在这些群里（请先在群设置里添加机器人）：{', '.join(missing)}")
    print(f"\n已写入 {DIR_MAP_PATH}")


if __name__ == "__main__":
    main()
