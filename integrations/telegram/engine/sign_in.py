import asyncio
import os
import sys

from amplifier_module_provider_openai_chatgpt import ChatGPTProvider


async def sign_in():
    provider = ChatGPTProvider({
        'auth_mode': 'chatgpt_codex',
        'token_file_path': os.environ['TELEGRAM_CODEX_TOKEN_FILE'],
    })
    try:
        async with asyncio.timeout(360):
            if provider.auth_status() != 'authenticated':
                await provider.login()
            if provider.auth_status() != 'authenticated':
                raise SystemExit('Codex sign-in did not complete.')
    except TimeoutError:
        raise SystemExit('Codex sign-in timed out. Run sign-in again to get a new code.') from None
    finally:
        await provider.close()
    print('Codex device sign-in complete.')


if __name__ == '__main__':
    if not sys.stdin.isatty():
        raise SystemExit('Run Codex device sign-in from an interactive terminal.')
    asyncio.run(sign_in())
