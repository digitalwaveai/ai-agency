from .beta_access import install_beta_access
from .bot import LeadPilotBot, run
from .database import Database
from .role_policy import install_role_policy
from .user_commands import install_user_commands


install_beta_access(LeadPilotBot, Database)
install_role_policy(LeadPilotBot)
install_user_commands(LeadPilotBot)


if __name__ == "__main__":
    run()
