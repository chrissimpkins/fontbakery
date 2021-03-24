import toml
import yaml


class Configuration(dict):
    def __init__(self, **kwargs):
        self.update(kwargs)
        for required_arg in ["custom_order", "explicit_checks", "exclude_checks"]:
            if required_arg not in self:
                self[required_arg] = None

    @classmethod
    def from_config_file(cls, filename):
        try:
            config = toml.load(filename)
        except toml.TomlDecodeError as e:
            # Try yaml
            config = yaml.safe_load(open(filename))
        if not isinstance(config, dict):
            raise Exception(f"Can't understand config file {imported}.")
        return cls(**config)

    def maybe_override(self, other):
        for key, value in other.items():
            if value is not None:
                self[key] = value
