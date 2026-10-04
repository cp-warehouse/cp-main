"""Compatibility entry point. Independent collectors own storage and checkpoints."""
import sys
import collect_dart
import collect_news
import collection_status


def translate(arguments):
    if not arguments or arguments[0] not in ('sync','news','show'):
        raise ValueError('사용법: company_news_pipeline.py sync|news|show (신규 명령: collect_dart.py / collect_news.py)')
    command,*options=arguments
    if command=='sync':
        return collect_dart.main,[value for value in options if value!='--enable-news']
    if command=='show':
        return collection_status.main,['--companies','1000',*options]
    return collect_news.main,[('--articles-per-company' if value=='--article-limit' else value) for value in options]


def main():
    target,options=translate(sys.argv[1:])
    sys.argv=[sys.argv[0],*options]
    return target()


if __name__=='__main__':
    sys.exit(main())
