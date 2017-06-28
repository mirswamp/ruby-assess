#! /bin/bash

p=`basename $0`

p_swamp=/p/swamp

## hack for vamshi's laptop environment
if [ ! -d $p_swamp ] ; then
    p_swamp=$HOME/$p_swamp
    echo $p: adjusting /p/swamp for vamshi
fi

update_platform=$p_swamp/frameworks/platform/update-platform

if [ ! -x $update_platform ] ; then
    echo $p: platform update tool missing/unusable 1>&2
    exit 1
fi


plat_list_file=util/all_platforms.txt
if [ ! -s "$plat_list_file" ] ; then
    echo $p: $plat_list_file: missing 1>&2
    exit 1
fi

## There are two kinds of platforms
## Full platforms which have binaries
## Alias platforms which are just a different platform name using
## a base platform

## grep allows comments in the platform list
all_plats=`grep -v '^#' $plat_list_file`
full_plats=
alias_plats=
for i in $all_plats ; do
    case $i in
	*=*)
	    alias_plats="$alias_plats $i"
	    ;;
	*)
	    full_plats="$full_plats $i"
	    ;;
    esac
done

## OLd style ...
## choose which ruby to incorporate into the image
ruby_binary=ruby-2.2.3-binaries
#ruby_binary=ruby-2.3.0-binaries
ruby_binary=ruby-2.4.0-binaries

## new style, pick an arch dir to incorporate
ruby_arch=$PWD/ruby-arch
## moved out of here into common AFS location.
ruby_arch=$p_swamp/frameworks/ruby/ruby-arch


## the name of the file containingthe checksums
checksumfile="md5sum"

## choose a platform dependent md5sum
MD5EXE="md5sum"

## special cases
case `uname -s` in
    Darwin)
	## XXX this doesn't have the same output as md5sum
	MD5EXE="md5"
	;;
esac


function md5_sum {
    local dest_dir="$1"

    (
	cd "$dest_dir"

	find . -type f ! -name "$checksumfile" -exec "$MD5EXE" '{}' ';' > "$checksumfile"
    )

}

function copy_scripts {

    local dest_dir="$1"
    local release_dir="$PWD/release"

    [[ -d "$release_dir/swamp-conf" ]] && \
	cp -r "$release_dir/swamp-conf" "$dest_dir"

    cp -p -r "$release_dir/in-files" "$dest_dir"

    local scripts_dir="$dest_dir/in-files/scripts"
    mkdir -p "$scripts_dir"

    ## XXX don't copy binaries, only machine-independent items
    #cp -r "$PWD/bin" $scripts_dir
    
    local lib_dir="$scripts_dir/lib"
    mkdir -p "$lib_dir"

    cp -r $PWD/lib/* "$lib_dir"

    local ruby_assess_dir="$lib_dir/ruby_assess"
    mkdir $ruby_assess_dir
    cp -r $PWD/src/* $ruby_assess_dir

    ## get the platform level files
    $update_platform --dir "$dest_dir/in-files"

    local version_dir="$lib_dir/version"
    mkdir -p $version_dir
    echo $new_version > $version_dir/version.txt
    echo '' > $version_dir/__init__.py

    (
	cd "$(dirname $scripts_dir)"
	tar -c -z --file="$(basename $scripts_dir)"".tar.gz" "$(basename $scripts_dir)"
	if test $? -eq 0; then
	    rm -rf "$(basename $scripts_dir)"
	fi
    )
}

function main {

    local framework="$(basename $PWD)"
    local new_version="${2:-$(git tag | sort -V | tail -n 1)}"
    local dest_dir="$1/$framework-$new_version"

    ## switch to noarch .. could also be "common", but we should
    ## try to make noarch work for platforms binaries aren't compiled
    ## for any platform
    local main_plat=noarch

    echo --- create main platform $main_plat
    [[ ! -d "$dest_dir/$main_plat" ]] && mkdir -p "$dest_dir/$main_plat"

    ## create main plat WITHOUT any platform-dependent content

    copy_scripts "$dest_dir/$main_plat"
    cp $PWD/release/{LICENSE.txt,RELEASE_NOTES.txt} "$dest_dir"


    ## shadow main platform BEFORE any machine-dependent content installed
    ## swamp-conf is a full-shadow (can be dir-symlink)
    ## in-files is a partial-shadow (needs to be a dir for bins)
    echo --- create base platforms as shadow copies
    for other_plat in $full_plats  ; do
	echo - $other_plat
	## filter out the main platform,
	case $other_plat in
	    $main_plat)
		echo filter out $other_plat main plat $main_plat
		continue
		;;
	esac

	## relative directory of the main platform, so all symlinks
	## in the product are relative to the root dir of a given
	## platform
	## release-dir/plat-dir/sub-dir
	main_plat_dir=../$main_plat
	for sub_dir in in-files swamp-conf; do
	    # echo $sub_dir
	    case $sub_dir in
		swamp-conf)
		    ## entire directory
		    (cd $dest_dir/$other_plat ;
		     ln -s $main_plat_dir/$sub_dir ./
		    )
		    ;;
		*)
		    ## files in the directory
		    ## Level is one deeper, hence ..
		    mkdir -p $dest_dir/$other_plat/$sub_dir
		    (cd $dest_dir/$other_plat/$sub_dir ;
		     find ../$main_plat_dir/$sub_dir -type f \
			  -exec ln -sf '{}' ';'
		    )
		    ;;
	    esac
	done
    done

    ## Now install machine-dependent content
    echo --- populate base platforms with binary content
    errs=false
    for plat in $full_plats ; do
	echo $plat
	#		local rvm="$PWD/$ruby_binary/ruby-binary---$plat/rvm.tar.gz"
	#		[[ -f "$rvm" ]] && cp "$rvm" "$dest_dir/$plat/in-files"

	## XXX change this to platform names so standard tools
	## can work OK.
	local rvm_dir="$ruby_arch/$plat"
	## bash doesn't run the loop if no expansions
	for f in ${rvm_dir}/* ; do
	    ## XXX need error checking here
	    cp -p "$f" "$dest_dir/$plat/in-files"
	    if [ $? -ne 0 ] ; then
		errs=true
	    fi
	done
    done
    ## don't let complete if something didn't copy
    ## let the errors accumualte so you can see them all at once.
    if $errs ; then
	echo $p: unable to install binaries, aborting 1>&2
	exit 1
    fi

    echo --- create alias platforms
    for plat in $alias_plats ; do
	## alias-plat=base-plat
	ap=`expr "$plat" : '\([^=]*\)=.*'`
	bp=`expr "$plat" : '[^=]*=\(.*\)'`

	echo $ap

	## error checking
	e=false
	if [ -z "$ap" ] ; then
	    echo $p: $plat: no alias-plat 1>&2
	    e=true
	fi
	if [ -z "$bp" ] ; then
	    echo $p: $plat: no base-plat 1>&2
	    e=true
	fi
	if [ ! -d $dest_dir/$bp ] ; then
	    echo $p: $bp: base-plat missing 1>&2
	    e=true
	fi
	if $e ; then
	    continue
	fi
	(cd $dest_dir ;
	 if ln -sf $bp $ap ; then
	     :
	 else
	     echo $p: $plat: alias-plat symlink fail 1>&2
	 fi
	)

    done

    md5_sum "$dest_dir"

}

if [ $# -lt 1 ] ; then
    echo usage: $p destination-dir [version] 1>&2
    exit 1
fi

main "$@"

