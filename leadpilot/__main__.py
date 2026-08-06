from . import bot as bot_module
from .analysis_insights import install_analysis_insights
from .beta_access import install_beta_access
from .bot import LeadPilotBot, run
from .compact_source_links import install_compact_source_links
from .database import Database
from .global_price_mode import install_global_price_mode
from .hide_settings_button import install_hide_settings_button
from .lead_action_buttons import install_lead_action_buttons
from .niche_profile import install_niche_profile
from .one_time_service_notice import install_one_time_service_notice
from .owner_emergency_actions import install_owner_emergency_actions
from .personal_lead_ids import install_personal_lead_ids
from .project_button_guard import install_project_button_guard
from .project_questionnaires import install_project_questionnaires
from .project_radars import install_project_radars
from .project_schema_hotfix import install_project_schema_hotfix
from .project_search_context import install_project_search_context
from .radar_inline_controls import install_radar_inline_controls
from .radar_menu_access_fix import install_radar_menu_access_fix
from .role_policy import install_role_policy
from .scheduled_radars import install_scheduled_radars
from .search_quality import install_search_quality
from .search_reliability import install_resilient_search
from .search_zero_fallback import install_zero_result_fallback
from .serpapi import SerpApiClient
from .telegram_command_menu import install_telegram_command_menu
from .trial_limits_guard import install_trial_limits_guard
from .trial_radar_paywall import install_trial_radar_paywall
from .usage_integrity import install_usage_integrity
from .usage_limits import install_usage_limits
from .user_commands import install_user_commands


install_beta_access(LeadPilotBot, Database)
install_personal_lead_ids(Database)
install_niche_profile(Database, LeadPilotBot)
install_project_questionnaires(LeadPilotBot, Database)
install_project_radars(LeadPilotBot, Database)
install_analysis_insights(LeadPilotBot, Database)
install_usage_limits(LeadPilotBot, Database)
install_project_schema_hotfix(Database)
install_search_quality(LeadPilotBot, SerpApiClient)
install_project_search_context(LeadPilotBot, SerpApiClient)
install_resilient_search(SerpApiClient)
install_zero_result_fallback(SerpApiClient)
install_role_policy(LeadPilotBot)
install_global_price_mode(LeadPilotBot)
install_project_button_guard(LeadPilotBot)
install_scheduled_radars(LeadPilotBot, Database)
install_radar_menu_access_fix(LeadPilotBot)
install_user_commands(LeadPilotBot)
install_radar_inline_controls(LeadPilotBot)
install_trial_limits_guard(LeadPilotBot)
install_hide_settings_button(LeadPilotBot)
install_trial_radar_paywall(LeadPilotBot)
install_usage_integrity(Database)
install_telegram_command_menu(LeadPilotBot)
install_lead_action_buttons(LeadPilotBot)
install_owner_emergency_actions(LeadPilotBot)
install_compact_source_links()
install_one_time_service_notice(LeadPilotBot)

# Меняем только текст пробного тарифа. Сами лимиты и их списание не затрагиваются.
bot_module.LIVE_TARIFFS_TEXT = bot_module.LIVE_TARIFFS_TEXT.replace(
    "20 поисков · 20 лидов · 20 анализов · 20 сообщений",
    "10 поисков · 10 лидов · 10 анализов · 10 сообщений",
)


if __name__ == "__main__":
    run()
