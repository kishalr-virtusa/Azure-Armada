"""Configuration from environment variables."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    """Application configuration from environment."""
    
    # Azure subscription
    azure_subscription_id: str = ""
    workload_rg: str = "rg-dra-workload"
    control_rg: str = "rg-dra-control"
    
    # Web Apps
    webapp_a_name: str = ""
    webapp_b_name: str = ""
    
    # Traffic Manager
    tm_profile_name: str = ""
    tm_fqdn: str = ""
    
    # SQL Failover Group
    sql_server_a: str = ""
    sql_server_b: str = ""
    sql_fog_name: str = ""
    
    # Azure OpenAI
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = "gpt-4.1-mini"
    
    # Storage
    storage_account_name: str = ""
    storage_table_endpoint: str = ""
    storage_blob_endpoint: str = ""
    
    # Console auth
    console_passcode: str = ""
    
    # Email notifications
    acs_connection_string: str = ""
    acs_sender: str = ""
    notify_email_to: str = ""
    
    # App Insights
    appinsights_connection_string: str = ""


def load_config() -> Config:
    """Load configuration from environment variables."""
    return Config(
        azure_subscription_id=os.getenv("AZURE_SUBSCRIPTION_ID", ""),
        workload_rg=os.getenv("WORKLOAD_RG", "rg-dra-workload"),
        control_rg=os.getenv("CONTROL_RG", "rg-dra-control"),
        
        webapp_a_name=os.getenv("WEBAPP_A_NAME", ""),
        webapp_b_name=os.getenv("WEBAPP_B_NAME", ""),
        
        tm_profile_name=os.getenv("TM_PROFILE_NAME", ""),
        tm_fqdn=os.getenv("TM_FQDN", ""),
        
        sql_server_a=os.getenv("SQL_SERVER_A", ""),
        sql_server_b=os.getenv("SQL_SERVER_B", ""),
        sql_fog_name=os.getenv("SQL_FOG_NAME", ""),
        
        azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        azure_openai_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"),
        
        storage_account_name=os.getenv("STORAGE_ACCOUNT_NAME", ""),
        storage_table_endpoint=os.getenv("STORAGE_TABLE_ENDPOINT", ""),
        storage_blob_endpoint=os.getenv("STORAGE_BLOB_ENDPOINT", ""),
        
        console_passcode=os.getenv("CONSOLE_PASSCODE", ""),
        
        acs_connection_string=os.getenv("ACS_CONNECTION_STRING", ""),
        acs_sender=os.getenv("ACS_SENDER", ""),
        notify_email_to=os.getenv("NOTIFY_EMAIL_TO", ""),
        
        appinsights_connection_string=os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", ""),
    )


# Singleton config instance
config = load_config()
