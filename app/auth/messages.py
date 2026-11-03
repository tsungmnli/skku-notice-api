"""Texts of the mails the auth flow sends (plain text, Korean)."""

SERVICE = "SKKU 공지"


def verify_email(link: str) -> tuple[str, str]:
    return (
        f"[{SERVICE}] 이메일 인증",
        f"{SERVICE} 가입을 완료하려면 아래 링크를 열어 이메일을 인증해 주세요.\n\n"
        f"{link}\n\n"
        "링크는 24시간 동안 유효합니다. 가입한 적이 없다면 이 메일을 무시해 주세요.\n",
    )


def already_registered(login_link: str, reset_link: str) -> tuple[str, str]:
    return (
        f"[{SERVICE}] 이미 가입된 이메일입니다",
        f"이 이메일로 {SERVICE} 가입 요청이 있었지만, 이미 가입된 계정이 있습니다.\n\n"
        f"로그인: {login_link}\n"
        f"비밀번호를 잊으셨다면: {reset_link}\n\n"
        "본인이 요청한 게 아니라면 이 메일을 무시해 주세요. 계정에는 아무 변화가 없습니다.\n",
    )


def reset_password(link: str) -> tuple[str, str]:
    return (
        f"[{SERVICE}] 비밀번호 재설정",
        "비밀번호를 재설정하려면 아래 링크를 열어 주세요.\n\n"
        f"{link}\n\n"
        "링크는 1시간 동안 유효하며, 재설정하면 모든 기기에서 로그아웃됩니다. "
        "요청한 적이 없다면 이 메일을 무시해 주세요.\n",
    )
