from collector import Parser, Settings, collect


class Quotes(Parser):
    name = 'quotes'
    start_urls = ['https://quotes.toscrape.com/']
    settings = Settings(concurrency=4, delay=0.5)

    async def parse(self, response):
        for quote in response.selector().css('div.quote'):
            yield {
                'text': quote.css('span.text::text').get(),
                'author': quote.css('small.author::text').get(),
            }

        next_page = response.selector().css('li.next a::attr(href)').get()
        if next_page:
            yield response.follow(next_page)


for quote in collect(Quotes):
    print(quote['author'])
