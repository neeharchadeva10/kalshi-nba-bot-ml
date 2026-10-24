def pytest_configure(config):
    config.addinivalue_line("markers", "slow: uses real hoopR box scores (about 10-20 s)")
