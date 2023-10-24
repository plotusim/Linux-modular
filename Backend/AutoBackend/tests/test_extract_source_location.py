from utils.func_utils import extract_source_location


def test_extract_source_location_1():
    file_attribute = "/net/ipv6/mcast_snoop"
    function_name = "ipv6_mc_check_icmpv6"
    file, start_loc, end_loc, _ = extract_source_location(file_attribute, function_name)
    print(f"Function {function_name} is defined at {file} line {start_loc}, end at line {end_loc}.")


def test_extract_source_location_2():
    file_attribute = "/net/sunrpc/xdr"
    function_name = "xdr_encode_array"
    file, start_loc, end_loc, _ = extract_source_location(file_attribute, function_name)
    print(f"Function {function_name} is defined at {file} line {start_loc}, end at line {end_loc}.")


def test_extract_source_location_3():
    file_attribute = "/net/netfilter/x_tables"
    function_name = "xt_register_matches"
    file, start_loc, end_loc, _ = extract_source_location(file_attribute, function_name)
    print(f"Function {function_name} is defined at {file} line {start_loc}, end at line {end_loc}.")


def test_extract_source_location_4():
    file_attribute = "/net/ipv6/esp6"
    function_name = "esp_input_done"
    file, start_loc, end_loc, _ = extract_source_location(file_attribute, function_name)
    print(f"Function {function_name} is defined at {file} line {start_loc}, end at line {end_loc}.")


if __name__ == '__main__':
    test_extract_source_location_1()
    test_extract_source_location_2()
    test_extract_source_location_3()
    test_extract_source_location_4()
