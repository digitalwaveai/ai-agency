from .bot import LeadPilotBot, run
from .user_commands import install_user_commands


install_user_commands(LeadPilotBot)


if __name__ == "__main__":
    run()
