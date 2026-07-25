from .beta_access import install_beta_access
from .bot import LeadPilotBot, run
from .database import Database
from .global_price_mode import install_global_price_mode
from .personal_lead_ids import install_personal_lead_ids
from .role_policy import install_role_policy
from .search_quality import install_search_quality
from .serpapi import SerpApiClient
from .usage_limits import install_usage_limits
from .user_commands import install_user_commands


install_beta_access(LeadPilotBot, Database)
install_personal_lead_ids(Database)
install_usage_limits(LeadPilotBot, Database)
install_search_quality(LeadPilotBot, SerpApiClient)
install_role_policy(LeadPilotBot)
install_global_price_mode(LeadPilotBot)
install_user_commands(LeadPilotBot)


if __name__ == "__main__":
    run()
