#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import ast, io, shutil, tokenize

P = Path("/home/wa_stopshop_mb/WA/Main_program/Fis/Panel.py")

TEXTS = {
"SI": """SPLOŠNI POGOJI POSLOVANJA

1. Splošno

Splošni pogoji poslovanja veljajo za aplikacijo, pri čemer se generalno uporabljajo splošni pogoji poslovanja lastnika aplikacije World Automatics d.o.o., objavljeni na spletni strani podjetja.

2. Lastnik aplikacije

World Automatics d.o.o., Župečja vas 6a, 2324 Lovrenc na Dravskem Polju, Slovenija

Davčna številka: SI88720098
Matična številka:7449933000
Elektronska pošta: info@worldautomatics.com
Telefonska številka: +386 41 599 403

Za splošne informacije, pomoč pri uporabi aplikacije, se lahko obrnete na naše osebje, in sicer od pon – pet, od 8:00 do 16:00 na telefonsko št. +386 41 599 403 ali nam pišite na E-pošta: info@worldautomatics.com.

V primeru nedelovanja in motenj v delovanju aplikacije pokličite na telefonsko številko: +386 64 223 237ali na E-pošto: support@worldautomatics.com

3. Opis aplikacije

Aplikacija ob vpisu vašega prodajnega avtomata, prikaže vstopno stran, ki vam omogoča takojšen pregled nad vsemi vašimi prodajnimi avtomati. Pri čemer iz vstopne strani izhaja dnevni promet, imena vaših avtomatov, prodajna mesta ter slikovni prikaz polnih oz. praznih prekatov. Nadalje se aplikacija razlikuje glede na paket, ki ga ima uporabnik zakupljenega.

Aplikacija je razdeljena v pakete dostopov in storitev, ki jih nudi. Dostop do vseh storitev, možnosti in nastavitev bo vsakemu uporabniku na voljo prve tri mesece od postavitve prodajnega avtomata. Uporabnikom aplikacije bo ob poteku treh mesecev posredovana ponudba za plačljive pakete.

4. Delovanje aplikacije

Aplikacija deluje preko naših strežnikov. Lastnik aplikacije bo v primeru predvidenih del uporabnike opozoril na morebitne motnje in nedelovanje aplikacije. Lastnik odškodninsko ne odgovarjal za nedelovanje ali motnje v delovanju aplikacije, pri čemer si bo prizadeval da bo aplikacija delovala brezhibno, vse morebitne napake pa bodo odpravljene v najkrajšem možnem času. Lastnik si prizadeva, da bo tehnične težave in motnje v delovanju aplikacije odpravil najkasneje v roku 2 delovnih dni. Lastnik aplikacije ne odgovarja za neposredno ali posredno škodo, povezano z nedelovanjem, motnjami v delovanju aplikacije, vključno z izgubo vrednosti premoženja uporabnika aplikacije, izgubo finančnih koristi ali stroški, potrebnimi za odpravo premoženjske škode, tudi če bi jo lastnik lahko predvidel ali bi jo moral predvideti.

5. Plačilo

Plačljive verzije aplikacije se obračunajo glede poslano ponudbo ter veljaven cenik v času nakupa. Cena nadgradnje aplikacije se obračunava mesečno preko trajnika. Lastnik si pridržuje pravico do posodabljanja cene nadgradnje aplikacije. Plačilo za aplikacijo preko tajnika se izvede vsakega 1. v mesecu. V primeru ne razpoložljivih sredstev na bančnem računu in zavrnitve trajnika bo uporabnik aplikacije o tem obveščen. Lastnik aplikacije bo uporabnika obvestil o neporavnani obveznosti. Uporabnik aplikacije se zavezuje zadevno obveznost poravnati v roku 7 dni od prejema pisnega poziva lastnika o neporavnanih obveznostih.

V primeru odprtih postavk na strani uporabnika aplikacije v višini dveh mesečnih obveznosti za plačilo nadgradnje aplikacije, se uporabniku posreduje pisni opomin in v njem skrajni rok za plačilo. Če uporabnik ne poravna celotnega dolgovanega zneska, si lastnik aplikacije pridržuje brez soglasja uporabnika, omejiti njegov dostop v aplikaciji na osnovno verzijo. Uporabnik aplikacije nima nobenih odškodninskih zahtev napram lastnika aplikacije. Takšen uporabnik nima pravice zahtevati nadgradnje aplikacije nadaljnjih 12 mesecev.

6. Varovanje podatkov

Lastnik aplikacije bo varoval osebne podatke uporabnikov aplikacije, pri čemer se zavezuje varovali osebne podatke v skladu z veljavno zakonodajo Republike Slovenije ter politiko zasebnosti objavljeno v aplikaciji.


POLITIKA ZASEBNOSTI

Upravljavec osebnih podatkov v skladu z Uredbo (EU) 2016/679 (GDPR) in Zakonom o varstvu osebnih podatkov (ZVOP-2) aplikacije World Automatics je World Automatics d.o.o. davčna številka: SI88720098, matična številka:7449933000, s sedežem: Župečja vas 6a, 2324 Lovrenc na Dravskem Polju, Slovenija, elektronska pošta: info@worldautomatics.com, telefonska številka: +386 41 599 403, v nadaljevanju »upravljalec«, v povezavi proizvodnjo drugih strojev in naprav za splošne namene.

Osebni podatki so obravnavani zakonito, pošteno in pregledno ter zbrani za določene, eksplicitne in legitimne namene ter ne dodatno obdelani na način, ki ni združljiv s temi nameni. Pravna podlaga obdelave podatkov temelji na prvem odstavku šestega člena uredbe (EU) 2016/679 Evropskega parlamenta in Sveta z dne 27. aprila 2016 o zaščiti fizičnih oseb pri obdelavi osebnih podatkov in o prostem pretoku teh podatkov ter o razveljavitvi Direktive 95/46/ES (Splošna uredba o varstvu podatkov) (besedilo z vidika EGP). Obdelava podatkov temelji na privolitvi (točka (a) prvega odstavka 6. člena GDPR) ter na izvajanju pogodbe oziroma izvedbi pred sklenitvijo pogodbe (točka (b) prvega odstavka 6. člena GDPR). Namen zbiranja osebnih podatkov temelji na personalizaciji aplikacije uporabniku aplikacije. Preko aplikacije upravljalec dostopa do osebnih podatkov potrebnih za delovanje in nadaljnji razvoj proizvodov ter upravljanje in odpravo napak. Upravljalec zbira podatke za pripravo prodajnih in tržnih analiz, statistike naprav ter diagnostičnih in razvojnih podatkov produktne linije.

Upravljalec osebne podatke strank obdeluje v okviru in za namen izvajanja pogodbenih razmerij.

Osebni podatki, ki se obdelujejo:

• Identifikacijski podatki (ime, priimek, naslov)
• Kontaktni podatki (email, telefon)
• Tehnični podatki (IP naslov, brskalnik, naprava)
• Osebni podatki: starost, spol, izobrazba, poklic;
• Digitalni podatki: številke SIM kartic, gesla in uporabniške prijave, posnetki video nadzora, IP naslovi ali drugi identifikatorji ter informacije, zbrane preko piškotkov ali podobnih tehnologij in v sistemskih dnevnikih;
• Podatki o transakcijah: informacije o nakupih, zgodovina transakcij, nakupovalne preference, količina in cena prodanih izdelkov glede na čas transakcije

Stranke priznavajo, da si podjetje v okviru doseganja zgoraj navedenih ciljev pridržuje pravico do pregleda prodajnih statistik posameznih naprav ter pravico do dostopa do posnetkov nadzornih kamer le za storitvene namene. Upravljalec vse na tak način pridobljene podatke varuje kot poslovno skrivnost. Upravljalec le na izrecno pisno zahtevo lastnika, posreduje pridobljene podatke. Podatki se obravnavajo v sodelovanja z zunanjimi izvajalci, ki zagotavljajo storitve podjetju (ponudniki IT storitev), pri čemer zagotavljamo, da tretje osebe, ki obdelujejo podatke, ravnajo na podlagi ustreznih pogodb o ne razkrivanju informacij (non-disclosure agreement (NDA)) in so zavezane k spoštovanju ustreznih standardov varnosti podatkov.

Upravljalec za namen učinkovitejšega izvajanja programa zvestobe v smislu člena 4(4) GDPR, ki vključuje avtomatizirano obdelavo osebnih podatkov z namenom ocenjevanja osebnih vidikov posameznika. Podatki so uporabljeni za analizo nakupnih navad, personaliziranih popustov, napovedovanje interesov ter prilagajanje ponudb in tržnih sporočil za izboljšanje vaše izkušnje. Takšna obdelava ne vključuje avtomatiziranega odločanja s pravnimi ali podobnimi bistvenimi učinki v smislu 22. člena GDPR.

S pisno zahtevo, poslano na naslov: World Automatics d.o.o., Župečja vas 6a, 2324 Lovrenc na Dravskem Polju, Slovenija, s pripisom »za GDPR«, ali na elektronski naslov info@worldautomatics.com, lahko posameznik zahteva, da se dane privolitve v obdelavo osebnih podatkov kadarkoli trajno ali začasno, v celoti ali delno prekličejo s pisno zahtevo, poslano na naveden naslov ali elektronski naslov, prav tako vsaka dopolnitev, popravek, blokiranje oz. omejitev obdelave, izbris osebnih podatkov ali ugovor obdelave podatkov, ki se obdelujejo v zvezi z njim, ter zahteva prenos podatkov.

World Automatics d.o.o. bo obdeloval osebne podatke v obsegu, ki je potreben za uresničevanje namenov obdelave in dokler bo to potrebno za dosego zasledovanega cilja oz. za potrebe zakonskih obveznosti, v primeru pridobljenih osebnih podatkov na podlagi privolitev, pa do preklica, razen v primerih, ko je rok hrambe osebnih podatkov določen z zakonom. V slednjih primerih upravljalec podatke hrani v skladu z zakonsko obveznostjo.""",

"EN": """GENERAL TERMS AND CONDITIONS

1. General

These General Terms and Conditions apply to the application. In general, the General Terms and Conditions of the application owner, World Automatics d.o.o., published on the company's website, apply.

2. Application owner

World Automatics d.o.o., Župečja vas 6a, 2324 Lovrenc na Dravskem Polju, Slovenia

Tax number: SI88720098
Company registration number: 7449933000
Email: info@worldautomatics.com
Telephone: +386 41 599 403

For general information and assistance with using the application, you can contact our staff Monday to Friday from 8:00 to 16:00 by telephone at +386 41 599 403 or by email at info@worldautomatics.com.

In the event of application malfunction or disruptions, call +386 64 223 237 or email support@worldautomatics.com.

3. Description of the application

After you enter your vending machine, the application displays a home page that gives you an immediate overview of all your vending machines. The home page shows daily turnover, the names of your machines, sales locations and a visual display of full or empty compartments. The application further differs according to the package purchased by the user.

The application is divided into packages of access and services. Access to all services, options and settings will be available to each user for the first three months after installation of the vending machine. At the end of the three-month period, application users will receive an offer for paid packages.

4. Operation of the application

The application operates through our servers. In the event of planned work, the application owner will notify users of possible disruptions and unavailability of the application. The owner does not assume liability for application unavailability or disruptions, while striving to ensure flawless operation and to remedy any errors as soon as possible. The owner aims to resolve technical difficulties and disruptions no later than within 2 working days. The application owner is not liable for direct or indirect damage related to application unavailability or disruptions, including loss of value of the application user's property, loss of financial benefits or costs necessary to remedy property damage, even if the owner could have foreseen or should have foreseen such damage.

5. Payment

Paid versions of the application are charged according to the submitted offer and the valid price list at the time of purchase. The application upgrade fee is charged monthly by direct debit. The owner reserves the right to update the price of the application upgrade. Payment for the application by direct debit is made on the 1st of each month. If there are insufficient funds in the bank account and the direct debit is rejected, the application user will be notified. The application owner will notify the user of the unpaid obligation. The application user undertakes to settle the obligation within 7 days of receiving the owner's written notice of unpaid obligations.

If the application user has outstanding items amounting to two monthly application-upgrade payments, the user will receive a written reminder with a final payment deadline. If the user does not settle the entire outstanding amount, the application owner reserves the right, without the user's consent, to restrict access in the application to the basic version. The application user has no compensation claims against the application owner. Such a user is not entitled to request an application upgrade for the following 12 months.

6. Data protection

The application owner will protect the personal data of application users and undertakes to protect personal data in accordance with the applicable legislation of the Republic of Slovenia and the Privacy Policy published in the application.


PRIVACY POLICY

The controller of personal data for the World Automatics application, in accordance with Regulation (EU) 2016/679 (GDPR) and the Slovenian Personal Data Protection Act (ZVOP-2), is World Automatics d.o.o., tax number SI88720098, company registration number 7449933000, with registered office at Župečja vas 6a, 2324 Lovrenc na Dravskem Polju, Slovenia, email info@worldautomatics.com, telephone +386 41 599 403, hereinafter the “controller”, in connection with the manufacture of other general-purpose machinery and equipment.

Personal data is processed lawfully, fairly and transparently and is collected for specified, explicit and legitimate purposes and is not further processed in a manner incompatible with those purposes. The legal basis for processing is Article 6(1) of Regulation (EU) 2016/679. Processing is based on consent (Article 6(1)(a) GDPR) and on performance of a contract or steps taken prior to entering into a contract (Article 6(1)(b) GDPR). The purpose of collecting personal data is personalization of the application for the application user. Through the application, the controller accesses personal data necessary for operation and further product development, management and troubleshooting. The controller collects data for sales and marketing analyses, device statistics, and diagnostic and development data for the product line.

The controller processes customers' personal data within and for the purpose of performing contractual relationships.

Personal data processed:

• Identification data (first name, surname, address)
• Contact data (email, telephone)
• Technical data (IP address, browser, device)
• Personal data: age, gender, education, occupation
• Digital data: SIM card numbers, passwords and user logins, video-surveillance recordings, IP addresses or other identifiers, and information collected through cookies or similar technologies and in system logs
• Transaction data: information about purchases, transaction history, shopping preferences, quantity and price of products sold according to transaction time

Customers acknowledge that, in pursuing the above objectives, the company reserves the right to review sales statistics of individual devices and to access surveillance-camera recordings only for service purposes. The controller protects all data obtained in this way as a business secret. The controller provides acquired data only upon an explicit written request by the owner. Data is processed in cooperation with external contractors providing services to the company (IT service providers), while ensuring that third parties processing the data act on the basis of appropriate non-disclosure agreements (NDA) and comply with appropriate data-security standards.

For more effective implementation of the loyalty programme within the meaning of Article 4(4) GDPR, the controller uses automated processing of personal data to evaluate personal aspects of an individual. The data is used to analyse purchasing habits, provide personalized discounts, predict interests and tailor offers and marketing communications to improve your experience. Such processing does not include automated decision-making producing legal or similarly significant effects within the meaning of Article 22 GDPR.

By written request sent to World Automatics d.o.o., Župečja vas 6a, 2324 Lovrenc na Dravskem Polju, Slovenia, marked “for GDPR”, or to info@worldautomatics.com, an individual may request that consent given for the processing of personal data be permanently or temporarily withdrawn at any time, in whole or in part. The individual may also request supplementation, correction, blocking or restriction of processing, deletion of personal data, object to processing of data concerning them, and request data portability.

World Automatics d.o.o. will process personal data to the extent necessary to achieve the purposes of processing and for as long as necessary to achieve the pursued objective or to meet legal obligations. Where personal data was obtained on the basis of consent, it will be processed until consent is withdrawn, except where the retention period is prescribed by law. In such cases, the controller retains the data in accordance with its legal obligation.""",

"DE": """ALLGEMEINE GESCHÄFTSBEDINGUNGEN

1. Allgemeines

Diese Allgemeinen Geschäftsbedingungen gelten für die Anwendung. Grundsätzlich gelten die auf der Website des Unternehmens veröffentlichten Allgemeinen Geschäftsbedingungen des Eigentümers der Anwendung, World Automatics d.o.o.

2. Eigentümer der Anwendung

World Automatics d.o.o., Župečja vas 6a, 2324 Lovrenc na Dravskem Polju, Slowenien

Steuernummer: SI88720098
Registrierungsnummer: 7449933000
E-Mail: info@worldautomatics.com
Telefon: +386 41 599 403

Für allgemeine Informationen und Hilfe bei der Nutzung der Anwendung können Sie sich montags bis freitags von 8:00 bis 16:00 Uhr unter +386 41 599 403 an unser Personal wenden oder uns unter info@worldautomatics.com schreiben.

Bei Funktionsstörungen oder Unterbrechungen der Anwendung rufen Sie +386 64 223 237 an oder schreiben Sie an support@worldautomatics.com.

3. Beschreibung der Anwendung

Nach der Eingabe Ihres Verkaufsautomaten zeigt die Anwendung eine Startseite an, die Ihnen einen sofortigen Überblick über alle Ihre Verkaufsautomaten ermöglicht. Auf der Startseite werden der Tagesumsatz, die Namen Ihrer Automaten, Verkaufsstellen sowie eine grafische Darstellung voller bzw. leerer Fächer angezeigt. Die Anwendung unterscheidet sich darüber hinaus je nach dem vom Nutzer gebuchten Paket.

Die Anwendung ist in Zugangs- und Dienstleistungspakete unterteilt. Der Zugang zu allen Dienstleistungen, Möglichkeiten und Einstellungen steht jedem Nutzer in den ersten drei Monaten nach Aufstellung des Verkaufsautomaten zur Verfügung. Nach Ablauf der drei Monate erhalten die Nutzer der Anwendung ein Angebot für kostenpflichtige Pakete.

4. Betrieb der Anwendung

Die Anwendung funktioniert über unsere Server. Bei geplanten Arbeiten wird der Eigentümer der Anwendung die Nutzer über mögliche Störungen und die Nichtverfügbarkeit der Anwendung informieren. Der Eigentümer übernimmt keine Schadensersatzhaftung für die Nichtverfügbarkeit oder Störungen der Anwendung, bemüht sich jedoch um einen einwandfreien Betrieb und darum, etwaige Fehler so schnell wie möglich zu beheben. Der Eigentümer ist bestrebt, technische Schwierigkeiten und Betriebsstörungen spätestens innerhalb von 2 Arbeitstagen zu beheben. Der Eigentümer der Anwendung haftet nicht für direkte oder indirekte Schäden im Zusammenhang mit der Nichtverfügbarkeit oder Störungen der Anwendung, einschließlich Wertverlust des Vermögens des Nutzers, Verlust finanzieller Vorteile oder Kosten zur Behebung von Sachschäden, selbst wenn der Eigentümer diese hätte vorhersehen können oder müssen.

5. Zahlung

Kostenpflichtige Versionen der Anwendung werden gemäß dem übermittelten Angebot und der zum Zeitpunkt des Kaufs gültigen Preisliste berechnet. Der Preis für das Upgrade der Anwendung wird monatlich per Lastschrift abgerechnet. Der Eigentümer behält sich das Recht vor, den Preis des Upgrades zu aktualisieren. Die Zahlung für die Anwendung per Lastschrift erfolgt jeweils am 1. des Monats. Bei unzureichender Deckung des Bankkontos und Ablehnung der Lastschrift wird der Nutzer der Anwendung darüber informiert. Der Eigentümer der Anwendung informiert den Nutzer über die offene Verpflichtung. Der Nutzer verpflichtet sich, die betreffende Verpflichtung innerhalb von 7 Tagen nach Erhalt der schriftlichen Aufforderung des Eigentümers bezüglich offener Verpflichtungen zu begleichen.

Bei offenen Posten des Nutzers in Höhe von zwei monatlichen Verpflichtungen für das Upgrade der Anwendung erhält der Nutzer eine schriftliche Mahnung mit einer letzten Zahlungsfrist. Begleicht der Nutzer den gesamten offenen Betrag nicht, behält sich der Eigentümer der Anwendung das Recht vor, den Zugang des Nutzers ohne dessen Zustimmung auf die Basisversion zu beschränken. Der Nutzer hat keine Schadensersatzansprüche gegenüber dem Eigentümer der Anwendung. Ein solcher Nutzer ist in den folgenden 12 Monaten nicht berechtigt, ein Upgrade der Anwendung zu verlangen.

6. Datenschutz

Der Eigentümer der Anwendung schützt die personenbezogenen Daten der Nutzer der Anwendung und verpflichtet sich, diese gemäß den geltenden Rechtsvorschriften der Republik Slowenien und der in der Anwendung veröffentlichten Datenschutzrichtlinie zu schützen.


DATENSCHUTZRICHTLINIE

Verantwortlicher für personenbezogene Daten der Anwendung World Automatics gemäß der Verordnung (EU) 2016/679 (DSGVO) und dem slowenischen Gesetz zum Schutz personenbezogener Daten (ZVOP-2) ist World Automatics d.o.o., Steuernummer SI88720098, Registrierungsnummer 7449933000, mit Sitz in Župečja vas 6a, 2324 Lovrenc na Dravskem Polju, Slowenien, E-Mail info@worldautomatics.com, Telefon +386 41 599 403, im Folgenden „Verantwortlicher“, im Zusammenhang mit der Herstellung sonstiger Maschinen und Geräte für allgemeine Zwecke.

Personenbezogene Daten werden rechtmäßig, nach Treu und Glauben und transparent verarbeitet und für festgelegte, eindeutige und legitime Zwecke erhoben; sie werden nicht in einer mit diesen Zwecken unvereinbaren Weise weiterverarbeitet. Die Rechtsgrundlage der Datenverarbeitung beruht auf Artikel 6 Absatz 1 der Verordnung (EU) 2016/679. Die Verarbeitung beruht auf einer Einwilligung (Artikel 6 Absatz 1 Buchstabe a DSGVO) sowie auf der Erfüllung eines Vertrags bzw. auf Maßnahmen vor Abschluss eines Vertrags (Artikel 6 Absatz 1 Buchstabe b DSGVO). Zweck der Erhebung personenbezogener Daten ist die Personalisierung der Anwendung für den Nutzer. Über die Anwendung greift der Verantwortliche auf personenbezogene Daten zu, die für den Betrieb und die weitere Entwicklung der Produkte sowie für Verwaltung und Fehlerbehebung erforderlich sind. Der Verantwortliche erhebt Daten zur Erstellung von Verkaufs- und Marketinganalysen, Gerätestatistiken sowie Diagnose- und Entwicklungsdaten der Produktlinie.

Der Verantwortliche verarbeitet personenbezogene Daten der Kunden im Rahmen und zum Zweck der Durchführung vertraglicher Beziehungen.

Verarbeitete personenbezogene Daten:

• Identifikationsdaten (Vorname, Nachname, Anschrift)
• Kontaktdaten (E-Mail, Telefon)
• Technische Daten (IP-Adresse, Browser, Gerät)
• Personenbezogene Daten: Alter, Geschlecht, Ausbildung, Beruf
• Digitale Daten: SIM-Kartennummern, Passwörter und Benutzeranmeldungen, Videoüberwachungsaufnahmen, IP-Adressen oder andere Identifikatoren sowie Informationen, die über Cookies oder ähnliche Technologien und in Systemprotokollen erfasst werden
• Transaktionsdaten: Informationen über Käufe, Transaktionshistorie, Einkaufspräferenzen, Menge und Preis der verkauften Produkte nach Transaktionszeitpunkt

Die Kunden erkennen an, dass sich das Unternehmen zur Erreichung der oben genannten Ziele das Recht vorbehält, Verkaufsstatistiken einzelner Geräte einzusehen und ausschließlich zu Servicezwecken auf Aufzeichnungen von Überwachungskameras zuzugreifen. Der Verantwortliche schützt alle auf diese Weise erlangten Daten als Geschäftsgeheimnis. Der Verantwortliche übermittelt die erlangten Daten nur auf ausdrückliche schriftliche Anfrage des Eigentümers. Die Daten werden in Zusammenarbeit mit externen Dienstleistern verarbeitet, die Dienstleistungen für das Unternehmen erbringen (IT-Dienstleister). Dabei wird sichergestellt, dass Dritte, die Daten verarbeiten, auf Grundlage geeigneter Geheimhaltungsvereinbarungen (Non-Disclosure Agreement, NDA) handeln und zur Einhaltung angemessener Datensicherheitsstandards verpflichtet sind.

Zum Zweck einer effizienteren Durchführung des Treueprogramms im Sinne von Artikel 4 Absatz 4 DSGVO nutzt der Verantwortliche eine automatisierte Verarbeitung personenbezogener Daten zur Bewertung persönlicher Aspekte einer Person. Die Daten werden zur Analyse des Kaufverhaltens, für personalisierte Rabatte, zur Vorhersage von Interessen sowie zur Anpassung von Angeboten und Marketingmitteilungen verwendet, um Ihre Erfahrung zu verbessern. Eine solche Verarbeitung umfasst keine automatisierte Entscheidungsfindung mit rechtlichen oder ähnlich erheblichen Auswirkungen im Sinne von Artikel 22 DSGVO.

Mit einer schriftlichen Anfrage an World Automatics d.o.o., Župečja vas 6a, 2324 Lovrenc na Dravskem Polju, Slowenien, mit dem Vermerk „für DSGVO“, oder per E-Mail an info@worldautomatics.com kann eine Person verlangen, dass erteilte Einwilligungen zur Verarbeitung personenbezogener Daten jederzeit dauerhaft oder vorübergehend, vollständig oder teilweise widerrufen werden. Ebenso kann sie Ergänzung, Berichtigung, Sperrung bzw. Einschränkung der Verarbeitung, Löschung personenbezogener Daten, Widerspruch gegen die Verarbeitung sie betreffender Daten sowie Datenübertragbarkeit verlangen.

World Automatics d.o.o. verarbeitet personenbezogene Daten in dem Umfang, der zur Erreichung der Verarbeitungszwecke erforderlich ist, und so lange, wie dies zur Erreichung des verfolgten Ziels oder zur Erfüllung gesetzlicher Verpflichtungen erforderlich ist. Personenbezogene Daten, die auf Grundlage einer Einwilligung erhoben wurden, werden bis zum Widerruf verarbeitet, außer wenn die Aufbewahrungsfrist gesetzlich vorgeschrieben ist. In diesen Fällen speichert der Verantwortliche die Daten entsprechend der gesetzlichen Verpflichtung.""",

"HR": """OPĆI UVJETI POSLOVANJA

1. Općenito

Ovi Opći uvjeti poslovanja vrijede za aplikaciju, pri čemu se općenito primjenjuju Opći uvjeti poslovanja vlasnika aplikacije World Automatics d.o.o., objavljeni na internetskoj stranici društva.

2. Vlasnik aplikacije

World Automatics d.o.o., Župečja vas 6a, 2324 Lovrenc na Dravskem Polju, Slovenija

Porezni broj: SI88720098
Matični broj: 7449933000
E-pošta: info@worldautomatics.com
Telefonski broj: +386 41 599 403

Za opće informacije i pomoć pri korištenju aplikacije možete se obratiti našem osoblju od ponedjeljka do petka od 8:00 do 16:00 na broj +386 41 599 403 ili nam pisati na info@worldautomatics.com.

U slučaju neispravnosti ili smetnji u radu aplikacije nazovite +386 64 223 237 ili pišite na support@worldautomatics.com.

3. Opis aplikacije

Nakon unosa vašeg prodajnog automata aplikacija prikazuje početnu stranicu koja vam omogućuje trenutačan pregled svih vaših prodajnih automata. Na početnoj stranici prikazuju se dnevni promet, nazivi vaših automata, prodajna mjesta te slikovni prikaz punih odnosno praznih pretinaca. Aplikacija se nadalje razlikuje ovisno o paketu koji je korisnik zakupio.

Aplikacija je podijeljena na pakete pristupa i usluga koje nudi. Pristup svim uslugama, mogućnostima i postavkama svakom će korisniku biti dostupan prva tri mjeseca od postavljanja prodajnog automata. Po isteku tri mjeseca korisnicima aplikacije bit će dostavljena ponuda za pakete koji se plaćaju.

4. Rad aplikacije

Aplikacija radi preko naših poslužitelja. U slučaju planiranih radova vlasnik aplikacije upozorit će korisnike na moguće smetnje i nedostupnost aplikacije. Vlasnik ne preuzima odštetnu odgovornost za nedostupnost ili smetnje u radu aplikacije, pri čemu će nastojati da aplikacija radi besprijekorno, a eventualne pogreške bit će otklonjene u najkraćem mogućem roku. Vlasnik nastoji tehničke poteškoće i smetnje u radu otkloniti najkasnije u roku od 2 radna dana. Vlasnik aplikacije ne odgovara za izravnu ili neizravnu štetu povezanu s nedostupnošću ili smetnjama u radu aplikacije, uključujući gubitak vrijednosti imovine korisnika aplikacije, gubitak financijskih koristi ili troškove potrebne za otklanjanje imovinske štete, čak i ako ju je vlasnik mogao ili trebao predvidjeti.

5. Plaćanje

Verzije aplikacije koje se plaćaju obračunavaju se prema poslanoj ponudi i važećem cjeniku u trenutku kupnje. Cijena nadogradnje aplikacije obračunava se mjesečno putem trajnog naloga. Vlasnik zadržava pravo ažuriranja cijene nadogradnje aplikacije. Plaćanje za aplikaciju putem trajnog naloga izvršava se svakog 1. u mjesecu. U slučaju nedostatnih sredstava na bankovnom računu i odbijanja trajnog naloga korisnik aplikacije bit će o tome obaviješten. Vlasnik aplikacije obavijestit će korisnika o nepodmirenoj obvezi. Korisnik aplikacije obvezuje se predmetnu obvezu podmiriti u roku od 7 dana od primitka pisanog poziva vlasnika o nepodmirenim obvezama.

U slučaju otvorenih stavki na strani korisnika aplikacije u visini dviju mjesečnih obveza za plaćanje nadogradnje aplikacije, korisniku se šalje pisana opomena s krajnjim rokom za plaćanje. Ako korisnik ne podmiri cijeli dugovani iznos, vlasnik aplikacije zadržava pravo bez suglasnosti korisnika ograničiti njegov pristup aplikaciji na osnovnu verziju. Korisnik aplikacije nema nikakvih odštetnih zahtjeva prema vlasniku aplikacije. Takav korisnik nema pravo zahtijevati nadogradnju aplikacije sljedećih 12 mjeseci.

6. Zaštita podataka

Vlasnik aplikacije štitit će osobne podatke korisnika aplikacije te se obvezuje štititi osobne podatke u skladu s važećim zakonodavstvom Republike Slovenije i Politikom privatnosti objavljenom u aplikaciji.


POLITIKA PRIVATNOSTI

Voditelj obrade osobnih podataka aplikacije World Automatics, u skladu s Uredbom (EU) 2016/679 (GDPR) i slovenskim Zakonom o zaštiti osobnih podataka (ZVOP-2), jest World Automatics d.o.o., porezni broj SI88720098, matični broj 7449933000, sa sjedištem na adresi Župečja vas 6a, 2324 Lovrenc na Dravskem Polju, Slovenija, e-pošta info@worldautomatics.com, telefonski broj +386 41 599 403, u nastavku „voditelj obrade“, u vezi s proizvodnjom ostalih strojeva i uređaja za opće namjene.

Osobni podaci obrađuju se zakonito, pošteno i transparentno te se prikupljaju u određene, izričite i legitimne svrhe i ne obrađuju se dalje na način koji nije u skladu s tim svrhama. Pravna osnova obrade podataka temelji se na članku 6. stavku 1. Uredbe (EU) 2016/679. Obrada podataka temelji se na privoli (članak 6. stavak 1. točka (a) GDPR-a) te na izvršavanju ugovora odnosno radnjama prije sklapanja ugovora (članak 6. stavak 1. točka (b) GDPR-a). Svrha prikupljanja osobnih podataka temelji se na personalizaciji aplikacije korisniku aplikacije. Putem aplikacije voditelj obrade pristupa osobnim podacima potrebnima za rad i daljnji razvoj proizvoda te upravljanje i otklanjanje pogrešaka. Voditelj obrade prikuplja podatke za pripremu prodajnih i marketinških analiza, statistiku uređaja te dijagnostičke i razvojne podatke proizvodne linije.

Voditelj obrade obrađuje osobne podatke stranaka u okviru i u svrhu izvršavanja ugovornih odnosa.

Osobni podaci koji se obrađuju:

• Identifikacijski podaci (ime, prezime, adresa)
• Kontaktni podaci (e-pošta, telefon)
• Tehnički podaci (IP adresa, preglednik, uređaj)
• Osobni podaci: dob, spol, obrazovanje, zanimanje
• Digitalni podaci: brojevi SIM kartica, lozinke i korisničke prijave, snimke videonadzora, IP adrese ili drugi identifikatori te informacije prikupljene putem kolačića ili sličnih tehnologija i u sistemskim zapisnicima
• Podaci o transakcijama: informacije o kupnjama, povijest transakcija, kupovne preferencije, količina i cijena prodanih proizvoda prema vremenu transakcije

Stranke potvrđuju da si društvo u okviru ostvarivanja prethodno navedenih ciljeva pridržava pravo pregleda prodajnih statistika pojedinih uređaja te pravo pristupa snimkama nadzornih kamera samo u servisne svrhe. Voditelj obrade sve tako dobivene podatke čuva kao poslovnu tajnu. Voditelj obrade dobivene podatke prosljeđuje samo na izričit pisani zahtjev vlasnika. Podaci se obrađuju u suradnji s vanjskim izvođačima koji društvu pružaju usluge (pružatelji IT usluga), pri čemu osiguravamo da treće osobe koje obrađuju podatke djeluju na temelju odgovarajućih ugovora o neotkrivanju informacija (non-disclosure agreement, NDA) i obvezane su poštovati odgovarajuće standarde sigurnosti podataka.

U svrhu učinkovitijeg provođenja programa vjernosti u smislu članka 4. stavka 4. GDPR-a, voditelj obrade koristi automatiziranu obradu osobnih podataka radi procjene osobnih aspekata pojedinca. Podaci se koriste za analizu kupovnih navika, personalizirane popuste, predviđanje interesa te prilagodbu ponuda i marketinških poruka radi poboljšanja vašeg iskustva. Takva obrada ne uključuje automatizirano donošenje odluka s pravnim ili sličnim značajnim učincima u smislu članka 22. GDPR-a.

Pisanim zahtjevom poslanim na adresu World Automatics d.o.o., Župečja vas 6a, 2324 Lovrenc na Dravskem Polju, Slovenija, s naznakom „za GDPR“, ili na e-adresu info@worldautomatics.com, pojedinac može zatražiti da se dane privole za obradu osobnih podataka u bilo kojem trenutku trajno ili privremeno, u cijelosti ili djelomično opozovu. Također može zatražiti dopunu, ispravak, blokiranje odnosno ograničenje obrade, brisanje osobnih podataka, uložiti prigovor na obradu podataka koji se na njega odnose te zahtijevati prijenos podataka.

World Automatics d.o.o. obrađivat će osobne podatke u opsegu potrebnom za ostvarivanje svrha obrade i onoliko dugo koliko je potrebno za postizanje cilja odnosno ispunjavanje zakonskih obveza. Ako su osobni podaci dobiveni na temelju privole, obrađuju se do opoziva privole, osim u slučajevima kada je rok čuvanja osobnih podataka određen zakonom. U tim slučajevima voditelj obrade čuva podatke u skladu sa zakonskom obvezom."""
}

src = P.read_text(encoding="utf-8")
ast.parse(src)

# Locate exact NAME = STRING assignments using tokenize, so quote style and string length do not matter.
line_offsets = [0]
for line in src.splitlines(keepends=True):
    line_offsets.append(line_offsets[-1] + len(line))

def apos(pos):
    row, col = pos
    return line_offsets[row - 1] + col

tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
targets = {f"TERMS_BODY_{x}" for x in ("SI","EN","DE","HR")}
repls = []

for i, tok in enumerate(tokens):
    if tok.type != tokenize.NAME or tok.string not in targets:
        continue
    j = i + 1
    while j < len(tokens) and tokens[j].type in (tokenize.NL, tokenize.NEWLINE):
        j += 1
    if j >= len(tokens) or tokens[j].string != "=":
        continue
    j += 1
    while j < len(tokens) and tokens[j].type in (tokenize.NL, tokenize.NEWLINE):
        j += 1
    if j >= len(tokens) or tokens[j].type != tokenize.STRING:
        raise SystemExit(f"SAFETY STOP: {tok.string} is not a simple string assignment")
    lang = tok.string.rsplit("_", 1)[1]
    repls.append((tok.string, apos(tok.start), apos(tokens[j].end), f"{tok.string} = {TEXTS[lang]!r}"))

names = [x[0] for x in repls]
if sorted(names) != sorted(targets):
    raise SystemExit(f"SAFETY STOP: expected {sorted(targets)}, found {sorted(names)}")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = P.with_name(P.name + f".backup_before_WORLD_AUTOMATICS_APP_LEGAL_{stamp}")
shutil.copy2(P, backup)

for _, a, b, new in sorted(repls, key=lambda x: x[1], reverse=True):
    src = src[:a] + new + src[b:]

ast.parse(src)
P.write_text(src, encoding="utf-8")

print("OK: replaced TERMS_BODY_SI/EN/DE/HR")
print("Backup:", backup)
print("Updated:", P)
