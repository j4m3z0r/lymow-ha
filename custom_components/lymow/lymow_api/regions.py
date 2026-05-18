from dataclasses import dataclass


@dataclass(frozen=True)
class RegionConfig:
    aws_region: str
    user_pool_id: str
    app_client_id: str
    identity_pool_id: str
    device_binding_api: str
    device_profile_api: str
    user_account_api: str
    iot_endpoint: str


REGIONS: dict[str, RegionConfig] = {
    "ap": RegionConfig(
        aws_region="ap-southeast-2",
        user_pool_id="ap-southeast-2_vNriuUNeQ",
        app_client_id="2ch3nqqr0usf5sadvcrj2hp6ll",
        identity_pool_id="ap-southeast-2:87d0fe24-16af-4189-b02f-984a7ed14ee0",
        device_binding_api="https://1sfa49lnl8.execute-api.ap-southeast-2.amazonaws.com/prod",
        device_profile_api="https://7k2iuc99h7.execute-api.ap-southeast-2.amazonaws.com/prod",
        user_account_api="https://l2gobpcoqc.execute-api.ap-southeast-2.amazonaws.com/prod",
        iot_endpoint="a3j5zqqo5iuph9-ats.iot.ap-southeast-2.amazonaws.com",
    ),
    "eu": RegionConfig(
        aws_region="eu-west-1",
        user_pool_id="eu-west-1_6qNPbnrrd",
        app_client_id="3h1sqv3hishjiofbv8giskjgb0",
        identity_pool_id="eu-west-1:c905a69c-0153-401a-a879-0c50b892015b",
        device_binding_api="",
        device_profile_api="",
        user_account_api="",
        iot_endpoint="a3j5zqqo5iuph9-ats.iot.eu-west-1.amazonaws.com",
    ),
    "us": RegionConfig(
        aws_region="us-east-2",
        user_pool_id="us-east-2_GAyiLkZQf",
        app_client_id="3ftv5jumkv375hic8dpdqodj8n",
        identity_pool_id="us-east-2:037db699-5df0-4ed2-92b8-0dd0f1843918",
        device_binding_api="https://453ahng0z4.execute-api.us-east-2.amazonaws.com/prod",
        device_profile_api="https://xuw7gtx113.execute-api.us-east-2.amazonaws.com/prod",
        user_account_api="https://6r8m5rxeth.execute-api.us-east-2.amazonaws.com/prod",
        iot_endpoint="a3j5zqqo5iuph9-ats.iot.us-east-2.amazonaws.com",
    ),
    "cn": RegionConfig(
        aws_region="ap-east-1",
        user_pool_id="ap-east-1_23Lf1WZer",
        app_client_id="46mirppdlu6mrbjd5bkiil0n20",
        identity_pool_id="ap-east-1:3e9265aa-f564-4083-8e1e-988e6cfdc446",
        device_binding_api="",
        device_profile_api="",
        user_account_api="",
        iot_endpoint="a3j5zqqo5iuph9-ats.iot.ap-east-1.amazonaws.com",
    ),
}
