import pytest
from pydantic import ValidationError

from resona_job_core import TEXT_EXECUTION_PROFILE, OrganizationSettings, default_settings


@pytest.mark.parametrize("field", ["execution_profiles", "default_execution_profile"])
def test_text_job_execution_profile_name_is_reserved(field):
    """Text jobs record TEXT_EXECUTION_PROFILE; a real profile of that name would make them ambiguous."""
    body = default_settings().model_dump(mode="json")
    if field == "execution_profiles":
        body["execution_profiles"][TEXT_EXECUTION_PROFILE] = {"audio": [], "document": []}
    else:
        body["default_execution_profile"] = TEXT_EXECUTION_PROFILE
    with pytest.raises(ValidationError, match="reserved for text jobs"):
        OrganizationSettings.model_validate(body)
