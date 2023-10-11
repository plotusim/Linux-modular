from utils.func_utils import extract_function_info


def test_extract_function_info_1():
    code = '''
        static bool cache_defer_req(struct cache_req *req, struct cache_head *item)
        {
            struct cache_deferred_req *dreq;

            if (req->thread_wait) {
                cache_wait_req(req, item);
                if (!test_bit(CACHE_PENDING, &item->flags))
                    return false;
            }
            dreq = req->defer(req);
            if (dreq == NULL)
                return false;
            setup_deferral(dreq, item, 1);
            if (!test_bit(CACHE_PENDING, &item->flags))

                cache_revisit_request(item);

            cache_limit_defers();
            return true;
        }
        '''

    print(code)
    return_type, param_types, body = extract_function_info(code)
    print(f"Return Type: {return_type}")
    print(f"Parameter Types: {param_types}")
    print(f"Body: {body}")


def test_extract_function_info_2():
    function_code = ['/* Return true if and only if a deferred request is queued. */\n',
                     '// this is a comment\n',
                     'int __get_compat_msghdr(struct msghdr *kmsg,\n',
                     '\t\t\tstruct compat_msghdr __user *umsg,\n',
                     '\t\t\tstruct sockaddr __user **save_addr,\n', '\t\t\tcompat_uptr_t *ptr, compat_size_t *len)\n',
                     '{\n', '\tstruct compat_msghdr msg;\n', '\tssize_t err;\n', '\n',
                     '\tif (copy_from_user(&msg, umsg, sizeof(*umsg)))\n', '\t\treturn -EFAULT;\n', '\n',
                     '\tkmsg->msg_flags = msg.msg_flags;\n', '\tkmsg->msg_namelen = msg.msg_namelen;\n', '\n',
                     '\tif (!msg.msg_name)\n', '\t\tkmsg->msg_namelen = 0;\n', '\n', '\tif (kmsg->msg_namelen < 0)\n',
                     '\t\treturn -EINVAL;\n', '\n', '\tif (kmsg->msg_namelen > sizeof(struct sockaddr_storage))\n',
                     '\t\tkmsg->msg_namelen = sizeof(struct sockaddr_storage);\n', '\n',
                     '\tkmsg->msg_control_is_user = true;\n',
                     '\tkmsg->msg_control_user = compat_ptr(msg.msg_control);\n',
                     '\tkmsg->msg_controllen = msg.msg_controllen;\n', '\n', '\tif (save_addr)\n',
                     '\t\t*save_addr = compat_ptr(msg.msg_name);\n', '\n',
                     '\tif (msg.msg_name && kmsg->msg_namelen) {\n', '\t\tif (!save_addr) {\n',
                     '\t\t\terr = move_addr_to_kernel(compat_ptr(msg.msg_name),\n',
                     '\t\t\t\t\t\t  kmsg->msg_namelen,\n', '\t\t\t\t\t\t  kmsg->msg_name);\n', '\t\t\tif (err < 0)\n',
                     '\t\t\t\treturn err;\n', '\t\t}\n', '\t} else {\n', '\t\tkmsg->msg_name = NULL;\n',
                     '\t\tkmsg->msg_namelen = 0;\n', '\t}\n', '\n', '\tif (msg.msg_iovlen > UIO_MAXIOV)\n',
                     '\t\treturn -EMSGSIZE;\n', '\n', '\tkmsg->msg_iocb = NULL;\n', '\t*ptr = msg.msg_iov;\n',
                     '\t*len = msg.msg_iovlen;\n', '\treturn 0;\n', '}\n']

    code = ''.join(function_code)

    print(code)
    return_type, param_types, body = extract_function_info(code)
    print(f"Return Type: {return_type}")
    print(f"Parameter Types: {param_types}")
    print(f"Body: {body}")


def test_extract_function_info_3():
    code = """
    struct nf_conntrack_helper *
    __nf_conntrack_helper_find(const char *name, u16 l3num, u8 protonum, (void* a))
    {
        struct nf_conntrack_helper *h;
        unsigned int i;
    
        for (i = 0; i < nf_ct_helper_hsize; i++) {
            hlist_for_each_entry_rcu(h, &nf_ct_helper_hash[i], hnode) {
                if (strcmp(h->name, name))
                    continue;
    
                if (h->tuple.src.l3num != NFPROTO_UNSPEC &&
                    h->tuple.src.l3num != l3num)
                    continue;
    
                if (h->tuple.dst.protonum == protonum)
                    return h;
            }
        }
        return NULL;
    }
    """
    print(code)
    return_type, param_types, body = extract_function_info(code)
    print(f"Return Type: {return_type}")
    print(f"Parameter Types: {param_types}")
    print(f"Body: {body}")


if __name__ == '__main__':
    test_extract_function_info_1()
    test_extract_function_info_2()
    test_extract_function_info_3()
