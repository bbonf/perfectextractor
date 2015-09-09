import ConfigParser
import codecs
import glob
import string
import os

from lxml import etree
from csv_utils import UnicodeWriter

TEI = {'ns': 'http://www.tei-c.org/ns/1.0'}
NL = 'nl'
MARKUP = u'**{}**'


def pp_to_text(pp):
    """
    Turns a present perfect into text.
    """
    return ' '.join([part for (part, is_verb) in pp if is_verb])


def get_adjacent_line_number(segment_number, i):
    """
    Returns the next segment number + i.
    """
    split = segment_number.split('s')
    adj = int(split[1]) + i
    return split[0] + 's' + str(adj)


def get_marked_sentence(sentence, pp):
    """
    Marks the present perfect in a full sentence.
    TODO: this is a bit iffy, another idea could be to compose the sentence from the remaining siblings
    """
    # To find the pp in the full text, just join all the parts of the pp
    pp_text = ' '.join([part for (part, _) in pp])

    # For the replacement, mark the verbs with the MARKUP
    pp_verbs = [part for (part, is_verb) in pp if is_verb]
    if len(pp) == len(pp_verbs):
        marked_pp = MARKUP.format(pp_text)
    else:
        marked_pp = ' '.join([MARKUP.format(part) if is_verb else part for (part, is_verb) in pp])
    
    return sentence.replace(pp_text, marked_pp)


class PerfectExtractor:
    def __init__(self, language_from, languages_to):
        self.l_from = language_from
        self.l_to = languages_to

        # Read the config
        config = ConfigParser.RawConfigParser()
        config.readfp(codecs.open('dpc.cfg', 'r', 'utf8'))
        self.config = config

    def is_nl(self):
        """
        Returns whether the current from language is Dutch (as integer).
        """
        return int(self.l_from == NL)

    def get_translated_lines(self, document, language_from, language_to, segment_number):
        """
        Returns the translated segment numbers (could be multiple) for a segment number in the original text.

        The translation document file format either ends with nl-en-tei.xml or nl-fr-tei.xml.

        An alignment line looks like this:
            <link type="A: 1-1" targets="p1.s1; p1.s1"/>
        
        To get from NL to EN/FR, we have to find the segment number in the targets attribute BEFORE the semicolon.
        For the reverse pattern, we have to find the segment number in the targets attribute AFTER the semicolon.
        
        To get from EN to FR or from FR to EN, we have to use NL as an in between language.

        This function supports 1-to-2 alignments, as it will return the translated lines as a list.

        TODO: deal with 2-to-2 and 2-to-1 alignments as well here.
        """
        result = []

        if NL in [language_from, language_to]: 
            not_nl = language_to if language_to != NL else language_from
            alignment_file = document + NL + '-' + not_nl + '-tei.xml'

            if os.path.isfile(alignment_file):
                alignment_tree = etree.parse(alignment_file)
                for link in alignment_tree.xpath('//ns:link', namespaces=TEI):
                    targets = link.get('targets').split('; ')
                    if segment_number in targets[1 - int(language_from == NL)].split(' '):
                        result = targets[int(language_from == NL)].split(' ')
                        break
        else:
            lookup = self.get_translated_lines(document, language_from, NL, segment_number)
            for lookup_number in lookup:
                lines = self.get_translated_lines(document, NL, language_to, lookup_number)
                result.extend(lines)

        return set(result)

    def get_line_by_number(self, tree, language_to, segment_number):
        """
        Returns the line for a segment number.
        TODO: handle more than one here? => bug
        """
        sentence = '-'
        pp = None

        line = tree.xpath('//ns:s[@n="' + segment_number + '"]', namespaces=TEI)
        if line:
            s = line[0]
            sentence = s.getprevious().text
            for e in s.xpath(self.config.get(language_to, 'xpath'), namespaces=TEI):
                pp = self.check_present_perfect(e, language_to)
                if pp: 
                    sentence = get_marked_sentence(s.getprevious().text, pp)
                    break

        return sentence, pp

    def get_original_language(self, document):
        """
        Returns the original language for a document.
        """
        metadata_tree = etree.parse(document + self.l_from + '-mtd.xml')
        return metadata_tree.getroot().find('metaTrans').find('Original').get('lang')

    def is_lexically_bound(self, language, aux_verb, perfect):
        """
        Checks if the perfect is lexically bound to the auxiliary verb.
        If not, we are not dealing with a present perfect here.
        """
        aux_be = self.config.get(language, 'lexical_bound')

        # Read the list of verbs that use 'to be' as auxiliary verb
        # TODO: read this only once for performance
        aux_be_list = []
        if aux_be:
            with codecs.open(language + '_aux_be.txt', 'rb', 'utf-8') as lexicon:
                aux_be_list = lexicon.read().split()

        # If lexical bounds do not exist or we're dealing with an auxiliary verb that is unbound, return True
        if not aux_be or aux_verb.get('lemma') != aux_be:
            return True
        # Else, check whether the perfect is in the list of bound verbs
        else:
            return perfect.get('lemma') in aux_be_list

    def check_present_perfect(self, element, language, check_ppc=True):
        """
        Checks whether this element is the start of a present perfect (or pp continuous).
        If it is, the present perfect is returned as a list.
        If not, None is returned.
        """
        perfect_tag = self.config.get(language, 'perfect_tag')
        check_ppc = check_ppc and self.config.getboolean(language, 'ppc')
        ppc_lemma = self.config.get(language, 'ppc_lemma')
        stop_tags = tuple(self.config.get(language, 'stop_tags').split(','))

        # Collect all parts of the present perfect as tuples with text and whether it's verb
        pp = [(element.text, True)]

        is_pp = False
        for sibling in element.itersiblings():
            # If the tag of the sibling is the perfect tag, we found a present perfect! 
            if sibling.get('ana') == perfect_tag:
                # Check if the sibling is lexically bound to the auxiliary verb
                if not self.is_lexically_bound(language, element, sibling):
                    break
                pp.append((sibling.text, True))
                is_pp = True
                # ... now check whether this is a present perfect continuous (by recursion)
                if check_ppc and sibling.get('lemma') == ppc_lemma:
                    ppc = self.check_present_perfect(sibling, language, False)
                    if ppc:
                        pp.extend(ppc[1:])
                break
            # Stop looking at punctuation or stop tags
            elif sibling.text in string.punctuation or sibling.get('ana').startswith(stop_tags):
                break
            # No break? Then add as a non-verb part
            else: 
                pp.append((sibling.text, False))

        return pp if is_pp else None

    def find_translated_present_perfects(self, document, language_to, translated_lines):
        translated_pps = []
        lines = []

        if translated_lines:
            # TODO: parse this only once for performance
            translated_tree = etree.parse(document + language_to + '-tei.xml')
            for t in translated_lines:
                #f.write(get_line_by_number(translated_tree, get_adjacent_line_number(t, -1)) + '\n')
                translation, translated_pp = self.get_line_by_number(translated_tree, language_to, t)
                translated_pp = pp_to_text(translated_pp) if translated_pp else ''
                
                translated_pps.append(translated_pp)
                lines.append(translation)
                #f.write(get_line_by_number(translated_tree, get_adjacent_line_number(t, 1)) + '\n')

        return translated_pps, lines

    def process_folder(self, dir_name):
        """
        Creates a result file and processes each file in a folder.
        """
        result_file = '-'.join([dir_name, self.l_from]) + '.csv'
        with open(result_file, 'wb') as f:
            f.write(u'\uFEFF'.encode('utf-8'))  # the UTF-8 BOM to hint Excel we are using that...
            csv_writer = UnicodeWriter(f, delimiter=';')

            header = [
                'document',
                'original_language',
                'present perfect ' + self.l_from,
                self.l_from]
            for language in self.l_to: 
                header.append('present perfect ' + language)
                header.append(language)
            csv_writer.writerow(header)

            for filename in glob.glob(dir_name + '/*[0-9]-' + self.l_from + '-tei.xml'):
                results = self.process_file(filename)
                csv_writer.writerows(results)

    def process_file(self, filename):
        """
        Processes a single file.
        """
        document = filename.split(self.l_from + '-tei.xml')[0]
        results = []

        tree = etree.parse(filename)
        for e in tree.xpath('//' + self.config.get(self.l_from, 'xpath'), namespaces=TEI):
            pp = self.check_present_perfect(e, self.l_from)

            if pp:
                result = [document[:-1], self.get_original_language(document)]

                result.append(pp_to_text(pp))
                #words_between = [part for (part, is_verb) in pp if not is_verb]
                #result.append(str(len(words_between)))

                # Write the complete segment with mark-up
                marked_sentence = get_marked_sentence(e.getparent().getprevious().text, pp)
                result.append(marked_sentence)

                # Find the translated lines
                segment_number = e.getparent().getparent().get('n')[4:]
                for language_to in self.l_to:
                    translated_lines = self.get_translated_lines(document, self.l_from, language_to, segment_number)
                    translated_present_perfect, translated_marked_sentence = self.find_translated_present_perfects(document, language_to, translated_lines)
                    result.append('\n'.join(translated_present_perfect))
                    result.append('\n'.join(translated_marked_sentence))

                results.append(result)

        return results


#for root, dirs, files in os.walk(os.getcwd()):
#    for d in dirs:
#        process_folder(d)
        #break
#process_folder('bal')

if __name__ == "__main__":
    en_extractor = PerfectExtractor('en', ['nl', 'fr'])
    en_extractor.process_folder('data/bmm')
    nl_extractor = PerfectExtractor('nl', ['en', 'fr'])
    nl_extractor.process_folder('data/bmm')
    fr_extractor = PerfectExtractor('fr', ['nl', 'en'])
    fr_extractor.process_folder('data/bmm')
